#!/usr/bin/env python3
"""중섭 에셋 저장소의 Live2D 모델을 웹에서 쓰기 좋게 옮겨 담는다.

  python3 scripts/sync_live2d.py --cn <Reverse-1999-CN-Asset 폴더> --out .

- live2d/roles/<모델>/ 의 moc3, 동작, 표정 파일을 그대로 복사
- 텍스처(PNG)는 가로세로 최대 2048px webp로 줄여서 용량을 1/3 정도로 (폰에서도 가볍게)
- moc3(모델 뼈대)는 gzip으로 묶어 .moc3.gz 로 저장 (사이트가 받아서 풀어 쓴다)
- 출품작 스킨 모델(3으로 시작하는 6자리)만. 기본 스킨부터 차례로 담는다
- GitHub Pages 한도(1GB) 때문에 --budget(MB)을 넘으면 더 담지 않고,
  남은 모델은 원본 저장소(raw.githubusercontent.com) 주소로 index에 적어 둔다 → 사이트는 그쪽에서 바로 불러온다
- 사이트가 찾아 쓰도록 live2d/index.json 에 스킨 번호 → 모델 경로 목록을 만든다
이미 옮긴 모델은 건너뛴다 (--force 면 다시).
"""
import gzip
import argparse
import json
import os
import re
import shutil

from PIL import Image

MAX_TEX = 2048
RAW = "https://raw.githubusercontent.com/myssal/Reverse-1999-CN-Asset/master/live2d/roles"


def dir_size(p):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(p) for f in fs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cn", required=True)
    ap.add_argument("--out", default=".")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--budget", type=int, default=900, help="이 저장소에 담을 최대 용량(MB)")
    ap.add_argument("--max-new", type=int, default=0, help="한 번에 새로 담을 모델 수 (0이면 전부)")
    ap.add_argument("--skip-from", default="", help="다른 보관함의 index.json 주소. 거기 이미 담긴 모델은 건너뛴다 (두 번째 보관함용)")
    a = ap.parse_args()
    src_root = os.path.join(a.cn, "live2d", "roles")
    dst_root = os.path.join(a.out, "live2d")
    os.makedirs(dst_root, exist_ok=True)
    idx_path = os.path.join(dst_root, "index.json")
    index = json.load(open(idx_path)) if os.path.exists(idx_path) and not a.force else {}
    new = 0
    used = dir_size(dst_root)
    budget = a.budget * 1024 * 1024
    entries = []
    for folder in os.listdir(src_root):
        m = re.search(r"(?<!\d)(3\d{5})(?!\d)", folder)
        src = os.path.join(src_root, folder)
        if not m or not os.path.isdir(src):
            continue
        mj = next((f for f in os.listdir(src) if f.endswith(".model3.json")), None)
        if mj:
            entries.append((m.group(1), folder, mj))
    if a.skip_from:
        import urllib.request
        other = json.load(urllib.request.urlopen(a.skip_from, timeout=60))
        done_elsewhere = {k for k, v in other.items() if v.get("path")}
        entries = [e for e in entries if e[0] not in done_elsewhere]
        for k in list(index):
            if k in done_elsewhere:
                index.pop(k)
        print(f"다른 보관함에 이미 있는 {len(done_elsewhere)}개는 건너뜀 → 이번 대상 {len(entries)}개")
    # 기본 스킨(끝 두 자리 01)부터, 그다음 번호 순
    entries.sort(key=lambda e: (e[0][-2:] != "01", e[0]))
    for skin, folder, mj in entries:
        src = os.path.join(src_root, folder)
        dst = os.path.join(dst_root, folder)
        model = json.load(open(os.path.join(src, mj), encoding="utf-8"))
        refs = model.get("FileReferences", {})
        exp_names = sorted({e.get("Name") for e in refs.get("Expressions") or []} |
                           {f[:-10] for f in (os.listdir(os.path.join(src, "expressions")) if os.path.isdir(os.path.join(src, "expressions")) else []) if f.endswith(".exp3.json")})
        if not a.force and os.path.exists(os.path.join(dst, mj)) and index.get(skin, {}).get("path"):
            continue
        if used > budget or (a.max_new and new >= a.max_new):
            # 용량이 다 찼거나 이번 회차 몫을 다 담았으면 원본 저장소 주소를 적어 둔다 (다음 회차에 다시 시도)
            index[skin] = {"remote": f"{RAW}/{folder}/{mj}", "motions": sorted((refs.get("Motions") or {}).keys()), "expressions": exp_names}
            continue
        os.makedirs(dst, exist_ok=True)

        def copy(rel):
            if not rel:
                return
            s = os.path.join(src, rel)
            if os.path.exists(s):
                os.makedirs(os.path.dirname(os.path.join(dst, rel)), exist_ok=True)
                shutil.copyfile(s, os.path.join(dst, rel))

        moc = refs.get("Moc")
        if moc and os.path.exists(os.path.join(src, moc)):
            with open(os.path.join(src, moc), "rb") as fi, gzip.open(os.path.join(dst, moc) + ".gz", "wb", compresslevel=9) as fo:
                shutil.copyfileobj(fi, fo)
        copy(refs.get("DisplayInfo"))
        copy(refs.get("Physics"))
        copy(refs.get("Pose"))
        for group in (refs.get("Motions") or {}).values():
            for mo in group:
                copy(mo.get("File"))
        for ex in refs.get("Expressions") or []:
            copy(ex.get("File"))
        # 표정 파일은 model3.json에 안 적혀 있는 경우가 많아서 폴더째 확인
        exp_dir = os.path.join(src, "expressions")
        expressions = []
        if os.path.isdir(exp_dir):
            for f in sorted(os.listdir(exp_dir)):
                if f.endswith(".exp3.json"):
                    copy(f"expressions/{f}")
                    expressions.append({"Name": f[:-10], "File": f"expressions/{f}"})
        if expressions and not refs.get("Expressions"):
            refs["Expressions"] = expressions
        texs = []
        for t in refs.get("Textures") or []:
            s = os.path.join(src, t)
            if not os.path.exists(s):
                continue
            im = Image.open(s)
            if max(im.size) > MAX_TEX:
                r = MAX_TEX / max(im.size)
                im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
            out_rel = re.sub(r"\.png$", ".webp", t)
            os.makedirs(os.path.dirname(os.path.join(dst, out_rel)), exist_ok=True)
            im.save(os.path.join(dst, out_rel), "WEBP", quality=88, method=5)
            texs.append(out_rel)
        refs["Textures"] = texs
        model["FileReferences"] = refs
        with open(os.path.join(dst, mj), "w", encoding="utf-8") as f:
            json.dump(model, f, ensure_ascii=False, separators=(",", ":"))
        index[skin] = {
            "path": f"live2d/{folder}/{mj}", "gz": True,
            "motions": sorted((refs.get("Motions") or {}).keys()),
            "expressions": exp_names,
        }
        used += dir_size(dst)
        new += 1
        print(f"✓ {folder}  (누적 {used // 1048576}MB)")
    with open(idx_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(index.items())), f, ensure_ascii=False, separators=(",", ":"))
    local = sum(1 for v in index.values() if v.get("path"))
    print(f"새로 옮긴 모델 {new}개 · 이 저장소 {local}개 · 원본에서 불러올 것 {len(index) - local}개 · 사용 {used // 1048576}MB")


if __name__ == "__main__":
    main()

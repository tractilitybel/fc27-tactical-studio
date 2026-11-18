"""Download the AI player-selection model (MobileSAM, MIT licence) into
fc27studio/models, verifying checksums. Used by the build; safe to re-run."""
import hashlib
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
DEST = os.path.join(HERE, "..", "fc27studio", "models")
BASE = "https://huggingface.co/Acly/MobileSAM/resolve/main/"
FILES = {
    "mobile_sam_image_encoder.onnx": "580f5fb648ea1062c0aabc26217aed56921985f03f0cbbd852bba81d760cc749",
    "sam_mask_decoder_multi.onnx": "8976b90a87ba50a6a72217a5ff994f7d25ce16f2229fcc1ed259e1294c622ffe",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    os.makedirs(DEST, exist_ok=True)
    for name, digest in FILES.items():
        path = os.path.join(DEST, name)
        if os.path.isfile(path) and sha256(path) == digest:
            print(f"ok      {name}")
            continue
        print(f"fetch   {name}")
        tmp = path + ".part"
        urllib.request.urlretrieve(BASE + name, tmp)
        if sha256(tmp) != digest:
            os.unlink(tmp)
            sys.exit(f"checksum mismatch for {name}")
        os.replace(tmp, path)
    with open(os.path.join(DEST, "LICENSE-MobileSAM.txt"), "w", encoding="utf-8") as f:
        f.write("MobileSAM (https://github.com/ChaoningZhang/MobileSAM) - MIT License.\n"
                "ONNX export: https://huggingface.co/Acly/MobileSAM (MIT).\n"
                "Based on Segment Anything by Meta AI (Apache 2.0).\n")
    print("models ready in", os.path.normpath(DEST))


if __name__ == "__main__":
    main()

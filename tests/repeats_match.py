"""repeats.py — the candidate filter and the picture test, without the network.

    python3 tests/repeats_match.py        (needs Pillow, like repeats.py itself)

The candidate rules each encode a way the houses print the same work
differently (39.25 / 39.4 / 39.5 in for 100 cm) or a way two works look the
same on paper (same title, same size). The picture test is run on a synthetic
painting, a re-photograph of it (colour cast, tighter crop), and things that
are not it.
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ingest"))
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps  # noqa: E402
import repeats                                                          # noqa: E402


def lot(i, size, title=None, medium="oil on canvas", sale="s1", date="2020-01-01"):
    return {"id": f"x:{sale}:{i}", "sale_id": sale, "sale_date": date, "artist_key": "a",
            "title": title, "medium": medium, "size": size, "image_url": None}


def pairs(*lots):
    return {(a["id"], b["id"]) for a, b in repeats.candidate_pairs(list(lots))}


def painting(seed):
    random.seed(seed)
    im = Image.new("RGB", (400, 300), (random.randrange(256), random.randrange(256), random.randrange(256)))
    d = ImageDraw.Draw(im)
    for _ in range(12):
        x, y = random.randrange(400), random.randrange(300)
        d.ellipse([x, y, x + random.randrange(40, 160), y + random.randrange(40, 160)],
                  fill=(random.randrange(256), random.randrange(256), random.randrange(256)))
    # paint, not clip art: a photographed edge is never one pixel wide
    return im.filter(ImageFilter.GaussianBlur(4))


def rephotograph(im):
    """What the second house does: warmer cast, darker, cropped a little tighter, in a mount."""
    im = ImageEnhance.Color(ImageEnhance.Brightness(im).enhance(0.85)).enhance(1.2)
    im = im.crop((4, 3, im.width - 5, im.height - 4)).resize((640, 480))
    return ImageOps.expand(im, border=40, fill=(245, 243, 238))


def main():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    a, b = lot(1, "39.25 x 39.25 in", "Bindu"), lot(2, "39.4 x 39.4", "Bindu", sale="s2")
    check("houses round 100 cm three ways", pairs(a, b) == {(a["id"], b["id"])})
    check("orientation does not matter", pairs(lot(1, "24 x 36 in"), lot(2, "36 x 24 in", sale="s2")))
    check("an inch apart is a different work", not pairs(lot(1, "39 x 39 in"), lot(2, "40 x 40 in", sale="s2")))
    check("two titles disagree", not pairs(lot(1, "69 x 59 in", "Kali"), lot(2, "69 x 59 in", "Diagonal", sale="s2")))
    check("Untitled does not contradict a title", pairs(lot(1, "69 x 59 in", "Untitled"), lot(2, "69 x 59 in", "Kali", sale="s2")))
    check("Untitled (Kali) counts as untitled", pairs(lot(1, "69 x 59 in", "Untitled (Kali)"), lot(2, "69 x 59 in", "Kali", sale="s2")))
    check("two lots in one sale are two works", not pairs(lot(1, "20 x 20 in"), lot(2, "20 x 20 in")))
    check("canvas is not paper", not pairs(lot(1, "20 x 20 in", medium="oil on canvas"),
                                            lot(2, "20 x 20 in", medium="gouache on paper", sale="s2")))
    check("unknown medium does not contradict", pairs(lot(1, "20 x 20 in", medium=None),
                                                       lot(2, "20 x 20 in", medium="gouache on paper", sale="s2")))

    orig = painting(1)
    s_orig = repeats.signature(orig)
    ok, rgb, edge = repeats.same_work(s_orig, repeats.signature(rephotograph(orig)))
    check(f"a re-photograph is the same work (rgb {rgb:.2f} edge {edge:.2f})", ok)
    ok, rgb, edge = repeats.same_work(s_orig, repeats.signature(painting(2)))
    check(f"another painting is not (rgb {rgb:.2f} edge {edge:.2f})", not ok)
    ok, rgb, edge = repeats.same_work(s_orig, repeats.signature(ImageOps.mirror(orig)))
    check(f"its mirror image is not (rgb {rgb:.2f} edge {edge:.2f})", not ok)

    for f in fails:
        print("FAIL", f)
    n = 12
    print(f"{n - len(fails)}/{n} repeat-match cases correct")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

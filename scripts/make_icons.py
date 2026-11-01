"""Generate the app icons (run once: python3 scripts/make_icons.py)."""
from PIL import Image, ImageDraw, ImageFont

def font(size):
    for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            pass
    return ImageFont.load_default()

def icon(n, path):
    s = 4  # supersample
    N = n * s
    im = Image.new("RGB", (N, N), "#0f5132")
    d = ImageDraw.Draw(im)
    # card
    w, h = int(N * .46), int(N * .62)
    for i, (dx, rot, fill, txt, col) in enumerate([(-.13, 0, "#ffffff", "A", "#1a1a1a"), (.13, 0, "#ffffff", "K", "#c62828")]):
        x0 = int(N / 2 + dx * N - w / 2); y0 = int(N * .19 + i * N * .04)
        d.rounded_rectangle([x0, y0, x0 + w, y0 + h], radius=int(N * .05), fill=fill, outline="#0b3d2a", width=int(N * .012))
        d.text((x0 + int(w * .12), y0 + int(h * .06)), txt, fill=col, font=font(int(N * .16)))
        d.text((x0 + w / 2, y0 + h * .62), "♠" if txt == "A" else "♥", fill=col, font=font(int(N * .22)), anchor="mm")
    im = im.resize((n, n), Image.LANCZOS)
    im.save(path)

icon(180, "icons/apple-touch-icon.png")
icon(192, "icons/icon-192.png")
icon(512, "icons/icon-512.png")

"""
Draw the placeholder mascot set (TODO.md U5, UQ4, Decision 48).

    python tools/make_mascot.py            # writes src/assets/mascot/<pose>_<scale>.png
    python tools/make_mascot.py --sheet    # also logs/ui_u5/mascot_sheet.png, a contact sheet

"Poco" is a small chat-bubble creature in the brand's sky blue with an amber beak. It appears only
in moments - the welcome, an empty page, a success, an error, waiting - never in the reading path
(TODO.md 6.1). These images are PLACEHOLDERS drawn with Pillow so the product has art from day
one; the files can be replaced one by one by an illustrator's work at the same names and sizes,
and nothing else changes (the licence of anything that replaces them must allow distribution
under the AGPL - UQ4).

Each pose is drawn on a 1240 grid, shrunk with premultiplied alpha to 128 px (100 %), 192 px
(150 %) and 256 px (200 %).
"""
import argparse
import os
import sys

from PIL import Image, ImageDraw

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(REPO, 'src', 'assets', 'mascot')
SCALES = {'1x': 128, '1_5x': 192, '2x': 256}
POSES = ('hello', 'thinking', 'happy', 'oops', 'sleepy', 'celebrate')
G = 1240                                 # a roomy grid: props (a hat, a wave, z z z) stay inside it

SKY, SKY_DARK, SKY_LIGHT = '#4DBBF0', '#2A93D6', '#9FDDF8'
AMBER, AMBER_DARK = '#FFB224', '#F0801E'
INK = '#14263D'
SLEEP = '#8DA6C8'
CHEEK = '#FF9E8A'
WHITE = '#FFFFFF'
DROP = '#8FD3F4'
CONFETTI = ('#FF6F91', '#FFC75F', '#4DBBF0', '#9B5DE5', '#00C9A7')


def rgba(colour, alpha=255):
    colour = colour.lstrip('#')
    return int(colour[0:2], 16), int(colour[2:4], 16), int(colour[4:6], 16), alpha


class Canvas:
    def __init__(self):
        self.image = Image.new('RGBA', (G, G), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

    def ellipse(self, box, fill, outline=None, width=0):
        self.draw.ellipse(box, fill=rgba(fill), outline=rgba(outline) if outline else None, width=width)

    def polygon(self, points, fill):
        self.draw.polygon(points, fill=rgba(fill))

    def line(self, points, fill, width):
        self.draw.line(points, fill=rgba(fill), width=width, joint='curve')
        r = width / 2
        for x, y in (points[0], points[-1]):
            self.draw.ellipse((x - r, y - r, x + r, y + r), fill=rgba(fill))

    def arc(self, box, start, end, fill, width):
        self.draw.arc(box, start, end, fill=rgba(fill), width=width)

    def rounded(self, box, radius, fill):
        self.draw.rounded_rectangle(box, radius=radius, fill=rgba(fill))


def body(c, lift=0):
    """The chat-bubble body: a round blue creature with a tail at the lower left."""
    cx, cy, r = 600, 700 - lift, 330
    c.polygon([(250, 700 - lift), (170, 860 - lift), (400, 800 - lift)], SKY_DARK)           # tail
    c.ellipse((cx - r, cy - r, cx + r, cy + r), SKY)
    c.ellipse((cx - r + 40, cy - r + 36, cx + r - 120, cy + 20), SKY_LIGHT)                   # a soft highlight
    c.ellipse((cx - r + 60, cy - r + 70, cx + r - 60, cy + r - 20), SKY)
    c.ellipse((cx - 205, cy + 20, cx + 205, cy + r - 30), '#BFEAFB')                          # the belly
    # feet
    c.ellipse((cx - 190, cy + r - 40, cx - 60, cy + r + 40), AMBER)
    c.ellipse((cx + 60, cy + r - 40, cx + 190, cy + r + 40), AMBER)
    return cx, cy


def beak(c, cx, cy):
    c.polygon([(cx - 56, cy + 60), (cx + 56, cy + 60), (cx, cy + 130)], AMBER)
    c.polygon([(cx - 56, cy + 60), (cx + 56, cy + 60), (cx, cy + 92)], AMBER_DARK)


def wings(c, cx, cy, left=0, right=0):
    """Little wings; a positive value raises that wing."""
    c.ellipse((cx - 370, cy + 20 - left, cx - 250, cy + 220 - left * 0.4), SKY_DARK)
    c.ellipse((cx + 250, cy + 20 - right, cx + 370, cy + 220 - right * 0.4), SKY_DARK)


def eyes(c, cx, cy, style='open', look=(0, 0)):
    ex, ey = 150, cy - 70
    for sign in (-1, 1):
        x = cx + sign * ex
        if style == 'open':
            c.ellipse((x - 52, ey - 70, x + 52, ey + 70), INK)
            c.ellipse((x - 28 + look[0], ey - 52 + look[1], x + 6 + look[0], ey - 18 + look[1]), WHITE)
        elif style == 'happy':
            c.arc((x - 62, ey - 40, x + 62, ey + 64), 200, 340, INK, 26)
        elif style == 'closed':
            c.line([(x - 56, ey), (x + 56, ey)], INK, 24)
        elif style == 'worried':
            c.ellipse((x - 50, ey - 62, x + 50, ey + 62), INK)
            c.ellipse((x - 26, ey - 46, x + 6, ey - 16), WHITE)
            slant = -1 if sign < 0 else 1
            c.line([(x - 70 * slant, ey - 110 - 18 * slant), (x + 70 * slant, ey - 150 + 18 * slant)], INK, 20)


def cheeks(c, cx, cy):
    for sign in (-1, 1):
        x = cx + sign * 245
        c.ellipse((x - 48, cy - 10, x + 48, cy + 50), CHEEK)


def mouth(c, cx, cy, style='smile'):
    y = cy + 150
    if style == 'smile':
        c.arc((cx - 70, y - 50, cx + 70, y + 40), 20, 160, INK, 20)
    elif style == 'open':
        c.ellipse((cx - 48, y - 20, cx + 48, y + 70), INK)
        c.ellipse((cx - 30, y + 28, cx + 30, y + 64), CHEEK)
    elif style == 'o':
        c.ellipse((cx - 22, y - 6, cx + 22, y + 38), INK)
    elif style == 'wavy':
        c.line([(cx - 70, y + 10), (cx - 35, y - 12), (cx, y + 10), (cx + 35, y - 12), (cx + 70, y + 10)], INK, 18)


def sparkle(c, x, y, size, fill=AMBER):
    c.polygon([(x, y - size), (x + size * 0.28, y - size * 0.28), (x + size, y), (x + size * 0.28, y + size * 0.28),
               (x, y + size), (x - size * 0.28, y + size * 0.28), (x - size, y), (x - size * 0.28, y - size * 0.28)], fill)


def pose_hello(c):
    cx, cy = body(c)
    wings(c, cx, cy, left=0, right=250)
    eyes(c, cx, cy)
    cheeks(c, cx, cy)
    beak(c, cx, cy)
    mouth(c, cx, cy, 'open')
    for dx, dy in ((420, -120), (470, -40)):                                    # a wave
        c.arc((cx + dx - 70, cy + dy - 70, cx + dx + 70, cy + dy + 70), 300, 60, AMBER, 22)


def pose_thinking(c):
    cx, cy = body(c)
    wings(c, cx, cy)
    eyes(c, cx, cy, look=(26, -26))
    cheeks(c, cx, cy)
    beak(c, cx, cy)
    mouth(c, cx, cy, 'o')
    for i, r in enumerate((26, 36, 48)):
        c.ellipse((cx + 270 + i * 70 - r, cy - 360 - i * 60 - r, cx + 270 + i * 70 + r, cy - 360 - i * 60 + r), SKY_LIGHT)


def pose_happy(c):
    cx, cy = body(c, lift=20)
    wings(c, cx, cy, left=140, right=140)
    eyes(c, cx, cy - 20, 'happy')
    cheeks(c, cx, cy - 20)
    beak(c, cx, cy - 20)
    mouth(c, cx, cy - 20, 'open')
    sparkle(c, cx - 400, cy - 330, 70)
    sparkle(c, cx + 410, cy - 280, 54)
    sparkle(c, cx + 330, cy - 420, 36)


def pose_oops(c):
    cx, cy = body(c)
    wings(c, cx, cy)
    eyes(c, cx, cy, 'worried')
    cheeks(c, cx, cy)
    beak(c, cx, cy)
    mouth(c, cx, cy, 'wavy')
    x, y = cx + 300, cy - 260                                                     # a sweat drop
    c.polygon([(x, y - 90), (x - 46, y + 10), (x + 46, y + 10)], DROP)
    c.ellipse((x - 50, y - 20, x + 50, y + 80), DROP)


def pose_sleepy(c):
    cx, cy = body(c)
    wings(c, cx, cy)
    eyes(c, cx, cy, 'closed')
    cheeks(c, cx, cy)
    beak(c, cx, cy)
    mouth(c, cx, cy, 'o')
    for i, size in enumerate((70, 52, 38)):                                       # z z z
        x, y = cx + 300 + i * 86, cy - 280 - i * 96
        c.line([(x - size, y - size), (x + size, y - size), (x - size, y + size), (x + size, y + size)], SLEEP, 18)


def pose_celebrate(c):
    cx, cy = body(c, lift=20)
    wings(c, cx, cy, left=200, right=200)
    eyes(c, cx, cy - 20, 'happy')
    cheeks(c, cx, cy - 20)
    beak(c, cx, cy - 20)
    mouth(c, cx, cy - 20, 'open')
    c.polygon([(cx - 120, cy - 300), (cx + 120, cy - 300), (cx, cy - 520)], AMBER)   # a party hat
    c.ellipse((cx - 38, cy - 556, cx + 38, cy - 480), '#FF6F91')
    c.line([(cx - 90, cy - 330), (cx + 90, cy - 330)], WHITE, 22)
    import random
    rng = random.Random(7)
    for _ in range(26):
        x, y = rng.randint(80, G - 80), rng.randint(40, 500)
        if (x - cx) ** 2 + (y - cy + 300) ** 2 < 260 ** 2:
            continue
        colour = CONFETTI[rng.randrange(len(CONFETTI))]
        c.rounded((x, y, x + rng.randint(26, 44), y + rng.randint(12, 22)), 6, colour)


DRAWERS = {'hello': pose_hello, 'thinking': pose_thinking, 'happy': pose_happy, 'oops': pose_oops,
           'sleepy': pose_sleepy, 'celebrate': pose_celebrate}


def render(pose: str, size: int) -> Image.Image:
    canvas = Canvas()
    DRAWERS[pose](canvas)
    return canvas.image.convert('RGBa').resize((size, size), Image.Resampling.LANCZOS).convert('RGBA')


def write() -> int:
    os.makedirs(OUT, exist_ok=True)
    count = 0
    for pose in POSES:
        for tag, size in SCALES.items():
            render(pose, size).save(os.path.join(OUT, f'{pose}_{tag}.png'), optimize=True)
            count += 1
    return count


def sheet(path: str) -> None:
    pad, size = 24, 256
    image = Image.new('RGBA', (len(POSES) * (size + pad) + pad, 2 * (size + pad) + pad))
    for row, background in enumerate(((247, 245, 242, 255), (27, 28, 32, 255))):
        for column, pose in enumerate(POSES):
            tile = Image.new('RGBA', (size + pad, size + pad), background)
            tile.alpha_composite(render(pose, size), (pad // 2, pad // 2))
            image.alpha_composite(tile, (pad // 2 + column * (size + pad), pad // 2 + row * (size + pad)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    image.save(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('--sheet', action='store_true', help="also write a contact sheet to logs/ui_u5/")
    args = parser.parse_args(argv)
    print(f"wrote {write()} images to {os.path.relpath(OUT, REPO)}")
    if args.sheet:
        sheet(os.path.join(REPO, 'logs', 'ui_u5', 'mascot_sheet.png'))
        print("contact sheet: logs/ui_u5/mascot_sheet.png")
    return 0


if __name__ == '__main__':
    sys.exit(main())

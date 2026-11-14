"""Draw the Homunculus mark: the agent's point sphere, lit from the upper left, and one bright point
going round it with a short fading trail.

Everything is a round point, so the sphere and the orbit read as one drawing. The mark avoids three
shapes that read as something else at small sizes: a line or trail rising off the sphere toward a
bright tip (a lit fuse), a full ring (Saturn) and a square point (a notification badge).

Colours are the console's tokens from web/src/design/tokens.css, converted from OKLCH to sRGB hex,
so the icon files need no CSS.

Run: uv run python scripts/make_icons.py
It writes web/public/icon*.svg and web/src/shell/markGeometry.ts, which the header mark draws from.
"""

import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def to_linear_rgb(lightness: float, chroma: float, hue: float) -> list[float]:
    """OKLCH to linear sRGB, with the matrices from the OKLab definition."""
    a = chroma * math.cos(math.radians(hue))
    b = chroma * math.sin(math.radians(hue))
    lc = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3
    mc = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3
    sc = (lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return [
        4.0767416621 * lc - 3.3077115913 * mc + 0.2309699292 * sc,
        -1.2684380046 * lc + 2.6097574011 * mc - 0.3413193965 * sc,
        -0.0041960863 * lc - 0.7034186147 * mc + 1.7076147010 * sc,
    ]


def gamma(x: float) -> float:
    return 12.92 * x if x <= 0.0031308 else 1.055 * x ** (1 / 2.4) - 0.055


def oklch(lightness: float, chroma: float, hue: float) -> str:
    rgb = to_linear_rgb(lightness, chroma, hue)
    return "#" + "".join(f"{round(max(0.0, min(1.0, gamma(c))) * 255):02x}" for c in rgb)


HI, MID, LO = oklch(0.96, 0.02, 215), oklch(0.70, 0.04, 232), oklch(0.42, 0.04, 245)
PAPER, RULE = oklch(0.135, 0.012, 235), oklch(0.26, 0.016, 235)

# The sphere, and the orbit its point travels: an ellipse tilted around it, in a 64-unit grid.
# AT is where the point rests, on the near side and clear of the sphere's face: at dead centre
# front it would sit on the sphere and read as a spot on it.
CX, CY, R = 30, 32, 17
RX, RY, ROT = 28, 9, -16
AT = 0.12


def tier(px: int) -> dict[str, float]:
    """Fewer, bigger points the smaller the icon, so it still reads in a browser tab."""
    if px <= 24:
        return {"n": 24, "dot": 0.15, "trail": 4, "step": 0.42, "tr": 2.6, "pt": 4.4}
    if px <= 40:
        return {"n": 60, "dot": 0.095, "trail": 6, "step": 0.3, "tr": 1.9, "pt": 3.6}
    return {"n": 220, "dot": 0.052, "trail": 10, "step": 0.2, "tr": 1.4, "pt": 3.0}


def tone(lit: float, px: int) -> str:
    """Small sizes drop the darkest tone, which vanishes into the ground below 40px."""
    if lit > 0.62 or (px <= 24 and lit > 0.25):
        return HI
    if lit > 0.3 or px <= 40:
        return MID
    return LO


def sphere_points(px: int) -> list[tuple[float, float, float, str]]:
    """Points spread evenly over a sphere (a Fibonacci lattice), front half only."""
    size = tier(px)
    n = int(size["n"]) * 2
    golden, tilt, light = math.pi * (3 - math.sqrt(5)), 0.5, (-0.5, 0.62, 0.6)
    points = []
    for i in range(n):
        y = 1 - (i / (n - 1)) * 2
        ring = math.sqrt(1 - y * y)
        x, z = math.cos(golden * i) * ring, math.sin(golden * i) * ring
        y, z = y * math.cos(tilt) - z * math.sin(tilt), y * math.sin(tilt) + z * math.cos(tilt)
        if z < 0.05:
            continue
        lit = max(0.0, x * light[0] + y * light[1] + z * light[2])
        radius = size["dot"] * R * (0.65 + 0.35 * z)
        points.append((CX + x * R, CY - y * R, radius, tone(lit, px)))
    return points


def at(t: float) -> tuple[float, float]:
    a = math.radians(ROT)
    x, y = RX * math.cos(t), RY * math.sin(t)
    return CX + x * math.cos(a) - y * math.sin(a), CY + x * math.sin(a) + y * math.cos(a)


def seen(t: float) -> float:
    """How much of an orbit point shows: on the far half it fades where the sphere covers it."""
    if math.sin(t) >= 0:
        return 1.0
    x, y = at(t)
    return min(1.0, max(0.0, (math.hypot(x - CX, y - CY) - R + 1) / 4))


def orbit(px: int) -> str:
    """The point and its trail. The point travels across the front of the sphere, so the trail
    follows on the near side and never rises off the top."""
    size = tier(px)
    count = int(size["trail"])
    out = []
    for i in range(count, 0, -1):
        t = AT + i * size["step"]
        x, y = at(t)
        opacity = (1 - i / (count + 1)) * 0.75 * seen(t)
        radius = size["tr"] * (1 - 0.45 * i / count)
        out.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{MID}" '
            f'opacity="{opacity:.2f}"/>'
        )
    x, y = at(AT)
    out.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{size["pt"]}" fill="{HI}"/>')
    return "".join(out)


def drawing(px: int) -> str:
    dots = "".join(
        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r:.2f}" fill="{c}"/>'
        for x, y, r, c in sphere_points(px)
    )
    return dots + orbit(px)


def svg(px: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" width="{px}" height="{px}">'
        f"{body}</svg>"
    )


def tile(px: int) -> str:
    """The icon on a rounded tile, for the browser tab and home screens that show it as drawn."""
    rx = 14 if px >= 100 else 12
    ground = (
        f'<rect width="64" height="64" rx="{rx}" fill="{PAPER}"/>'
        f'<rect x="0.5" y="0.5" width="63" height="63" rx="{rx - 0.5}" '
        f'fill="none" stroke="{RULE}"/>'
    )
    return svg(px, ground + drawing(px))


def maskable(px: int) -> str:
    """A full-bleed square for launchers that cut their own shape. The drawing sits inside the
    central 80%, the safe zone the web app manifest spec says is never cut."""
    shrink = "translate(32 32) scale(0.78) translate(-32 -32)"
    ground = f'<rect width="64" height="64" fill="{PAPER}"/>'
    return svg(px, f'{ground}<g transform="{shrink}">{drawing(px)}</g>')


def geometry_ts() -> str:
    """The header mark's sphere, for the React component that moves the orbiting point itself."""
    size = tier(24)
    names = {HI: "hi", MID: "mid", LO: "lo"}
    points = ", ".join(
        f'[{x:.2f}, {y:.2f}, {r:.2f}, "{names[c]}"]' for x, y, r, c in sphere_points(24)
    )
    return (
        "// Generated by scripts/make_icons.py from the app icons' drawing. Do not edit by hand.\n"
        'export type Tone = "hi" | "mid" | "lo";\n'
        f"export const SPHERE: [number, number, number, Tone][] = [{points}];\n"
        f"export const ORBIT = {{ cx: {CX}, cy: {CY}, r: {R}, "
        f"rx: {RX}, ry: {RY}, rot: {ROT}, at: {AT} }};\n"
        f"export const TRAIL = {{ count: {int(size['trail'])}, step: {size['step']}, "
        f"r: {size['tr']}, point: {size['pt']} }};\n"
    )


def main() -> None:
    public = ROOT / "web" / "public"
    (public / "icon.svg").write_text(tile(24))
    (public / "icon-192.svg").write_text(tile(192))
    (public / "icon-512.svg").write_text(tile(512))
    (public / "icon-maskable.svg").write_text(maskable(512))
    (ROOT / "web" / "src" / "shell" / "markGeometry.ts").write_text(geometry_ts())
    print("Wrote web/public/icon*.svg and web/src/shell/markGeometry.ts")


if __name__ == "__main__":
    main()

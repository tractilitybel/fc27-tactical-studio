"""Plain-Python project data model (no Qt) — annotations, bookmarks, project."""
from __future__ import annotations

import copy
import math
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

Point = tuple[float, float]


def new_id() -> str:
    return uuid.uuid4().hex[:12]


class Kind:
    ARROW = "arrow"
    CURVED_ARROW = "curved_arrow"
    RUN = "run"              # dashed movement arrow
    LINE = "line"
    FREEHAND = "freehand"
    CIRCLE = "circle"        # ellipse in bbox of 2 points
    RECT = "rect"
    ZONE = "zone"            # filled polygon
    TEXT = "text"
    SPOTLIGHT = "spotlight"  # darkens everything except an ellipse
    CUTOUT = "cutout"        # a player lifted out of the frame and moved
    TOKEN = "token"          # numbered player disc (tactics board)
    BALL = "ball"
    MEASURE = "measure"      # distance line (metres on the board)

    ALL = [ARROW, CURVED_ARROW, RUN, LINE, FREEHAND, CIRCLE, RECT, ZONE, TEXT, SPOTLIGHT]
    VIDEO_TOOLS = ALL + [CUTOUT]
    BOARD_TOOLS = [TOKEN, BALL, ARROW, CURVED_ARROW, RUN, LINE, FREEHAND, CIRCLE, RECT, ZONE,
                   TEXT, MEASURE]
    EVERY = ALL + [CUTOUT, TOKEN, BALL, MEASURE]
    LABELS = {
        ARROW: "Arrow", CURVED_ARROW: "Curved Arrow", RUN: "Run (dashed)",
        LINE: "Line", FREEHAND: "Freehand", CIRCLE: "Circle", RECT: "Rectangle",
        ZONE: "Zone", TEXT: "Text", SPOTLIGHT: "Spotlight", CUTOUT: "Player cut-out",
        TOKEN: "Player token", BALL: "Ball", MEASURE: "Measure distance",
    }


class Layer:
    BOTH = "both"
    BEFORE = "before"
    AFTER = "after"


@dataclass
class Annotation:
    kind: str
    points: list[Point]
    start_frame: int
    end_frame: int
    id: str = field(default_factory=new_id)
    color: str = "#ffd400"
    width: float = 4.0
    opacity: float = 1.0
    fill_opacity: float = 0.25
    rotation: float = 0.0
    text: str = ""
    font_size: float = 28.0
    group_id: Optional[str] = None
    z: int = 0
    visible: bool = True
    locked: bool = False
    name: str = ""
    layer: str = "both"                  # before/after comparison: both | before | after
    # Player cut-outs
    image: str = ""                      # base64 PNG (RGBA) of the player
    fill_image: str = ""                 # base64 PNG (RGBA) hiding the player at its origin
    crop: list = field(default_factory=list)            # [x, y, w, h] of both images in the frame
    source_points: list = field(default_factory=list)   # outline at the original position
    ghost_opacity: float = 0.35          # 0 = original removed, 1 = original left untouched
    show_link: bool = True               # dashed outline + arrow from original to new position

    # ---------------------------------------------------------------- geometry
    def bbox(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points] or [0.0]
        ys = [p[1] for p in self.points] or [0.0]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        if self.kind in (Kind.TOKEN, Kind.BALL) and self.points:
            r = self.token_radius()
            cx, cy = self.points[0]
            return cx - r, cy - r, cx + r, cy + r
        if self.kind == Kind.TEXT:
            # Approximate text extent (renderer refines it).
            lines = (self.text or " ").split("\n")
            w = max(len(l) for l in lines) * self.font_size * 0.6
            h = len(lines) * self.font_size * 1.3
            x1, y1 = x0 + max(w, 10), y0 + max(h, 10)
        return x0, y0, x1, y1

    def token_radius(self) -> float:
        return max(4.0, self.font_size * (0.45 if self.kind == Kind.BALL else 0.75))

    def center(self) -> Point:
        x0, y0, x1, y1 = self.bbox()
        return (x0 + x1) / 2, (y0 + y1) / 2

    def translate(self, dx: float, dy: float) -> None:
        self.points = [(x + dx, y + dy) for x, y in self.points]

    def scale(self, sx: float, sy: float, anchor: Point) -> None:
        ax, ay = anchor
        self.points = [(ax + (x - ax) * sx, ay + (y - ay) * sy) for x, y in self.points]
        if self.kind in (Kind.TEXT, Kind.TOKEN, Kind.BALL):
            self.font_size = max(6.0, self.font_size * abs(sy))

    def visible_at(self, frame: int) -> bool:
        return self.visible and self.start_frame <= frame <= self.end_frame

    def to_local(self, p: Point) -> Point:
        """Undo rotation around the centre (for hit tests)."""
        if not self.rotation:
            return p
        cx, cy = self.center()
        a = -math.radians(self.rotation)
        dx, dy = p[0] - cx, p[1] - cy
        return (cx + dx * math.cos(a) - dy * math.sin(a),
                cy + dx * math.sin(a) + dy * math.cos(a))

    def hit(self, p: Point, tol: float) -> bool:
        x, y = self.to_local(p)
        x0, y0, x1, y1 = self.bbox()
        if self.kind in (Kind.TOKEN, Kind.BALL) and self.points:
            cx, cy = self.points[0]
            return math.hypot(x - cx, y - cy) <= self.token_radius() + tol
        if self.kind == Kind.CUTOUT and len(self.points) >= 3:
            return point_in_polygon((x, y), self.points)
        if self.kind in (Kind.TEXT, Kind.RECT, Kind.SPOTLIGHT, Kind.ZONE, Kind.CIRCLE):
            if self.kind == Kind.CIRCLE or self.kind == Kind.SPOTLIGHT:
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                rx, ry = max((x1 - x0) / 2, 1), max((y1 - y0) / 2, 1)
                d = ((x - cx) / (rx + tol)) ** 2 + ((y - cy) / (ry + tol)) ** 2
                return d <= 1.0
            if self.kind == Kind.ZONE and len(self.points) >= 3:
                return point_in_polygon((x, y), self.points) or \
                    polyline_distance((x, y), self.points + [self.points[0]]) <= tol
            return x0 - tol <= x <= x1 + tol and y0 - tol <= y <= y1 + tol
        pts = self.points
        if self.kind == Kind.CURVED_ARROW and len(pts) >= 3:
            pts = quad_bezier_points(pts[0], pts[1], pts[2], 24)
        return polyline_distance((x, y), pts) <= tol + self.width / 2

    def clone(self, new_identity: bool = True) -> "Annotation":
        a = copy.deepcopy(self)
        if new_identity:
            a.id = new_id()
        return a

    def source_transform(self) -> tuple[float, float, float, float]:
        """Cut-outs: (scale_x, scale_y, dx, dy) mapping original → current position."""
        if not self.source_points or len(self.source_points) != len(self.points):
            return 1.0, 1.0, 0.0, 0.0
        sx0 = min(p[0] for p in self.source_points)
        sy0 = min(p[1] for p in self.source_points)
        sw = max(p[0] for p in self.source_points) - sx0 or 1.0
        sh = max(p[1] for p in self.source_points) - sy0 or 1.0
        x0, y0, x1, y1 = self.bbox()
        kx, ky = (x1 - x0) / sw, (y1 - y0) / sh
        return kx, ky, x0 - sx0 * kx, y0 - sy0 * ky

    def to_dict(self) -> dict:
        d = asdict(self)
        d["points"] = [list(p) for p in self.points]
        d["source_points"] = [list(p) for p in self.source_points]
        if self.kind != Kind.CUTOUT:  # keep files small and readable
            for k in ("image", "fill_image", "crop", "source_points", "ghost_opacity", "show_link"):
                d.pop(k, None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Annotation":
        d = dict(d)
        d["points"] = [tuple(p) for p in d.get("points", [])]
        d["source_points"] = [tuple(p) for p in d.get("source_points", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def point_in_polygon(p: Point, poly: list[Point]) -> bool:
    x, y = p
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xi = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if x < xi:
                inside = not inside
    return inside


def segment_distance(p: Point, a: Point, b: Point) -> float:
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L = dx * dx + dy * dy
    if L == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / L))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def polyline_distance(p: Point, pts: list[Point]) -> float:
    if not pts:
        return float("inf")
    if len(pts) == 1:
        return math.hypot(p[0] - pts[0][0], p[1] - pts[0][1])
    return min(segment_distance(p, pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def quad_bezier_points(a: Point, c: Point, b: Point, n: int) -> list[Point]:
    out = []
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * a[0] + 2 * u * t * c[0] + t * t * b[0],
                    u * u * a[1] + 2 * u * t * c[1] + t * t * b[1]))
    return out


DEFAULT_CATEGORIES = [
    {"name": "Goal", "color": "#2ecc71"},
    {"name": "Chance", "color": "#27ae60"},
    {"name": "Defensive Error", "color": "#e74c3c"},
    {"name": "Pressing", "color": "#e67e22"},
    {"name": "Build-up", "color": "#3498db"},
    {"name": "Transition", "color": "#9b59b6"},
    {"name": "Set Piece", "color": "#f1c40f"},
    {"name": "Positioning", "color": "#1abc9c"},
    {"name": "Other", "color": "#95a5a6"},
]


@dataclass
class Bookmark:
    frame: int
    title: str = ""
    category: str = "Other"
    tags: list[str] = field(default_factory=list)
    players: list[str] = field(default_factory=list)
    notes: str = ""
    end_frame: Optional[int] = None
    rating: int = 0  # 0-5 importance
    id: str = field(default_factory=new_id)
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Bookmark":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def matches(self, text: str) -> bool:
        if not text:
            return True
        t = text.lower()
        hay = " ".join([self.title, self.category, self.notes, *self.tags, *self.players]).lower()
        return all(word in hay for word in t.split())


@dataclass
class Board:
    """A 2D tactics board: top-down pitch with tokens and drawings."""
    name: str = "Board"
    annotations: list[Annotation] = field(default_factory=list)
    linked_frame: Optional[int] = None   # video moment this board explains
    home_color: str = "#e63946"
    away_color: str = "#3a86ff"
    notes: str = ""
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict:
        return {"name": self.name, "id": self.id, "linked_frame": self.linked_frame,
                "home_color": self.home_color, "away_color": self.away_color, "notes": self.notes,
                "annotations": [a.to_dict() for a in self.annotations]}

    @classmethod
    def from_dict(cls, d: dict) -> "Board":
        return cls(name=d.get("name", "Board"), id=d.get("id") or new_id(),
                   linked_frame=d.get("linked_frame"), home_color=d.get("home_color", "#e63946"),
                   away_color=d.get("away_color", "#3a86ff"), notes=d.get("notes", ""),
                   annotations=[Annotation.from_dict(a) for a in d.get("annotations", [])])


@dataclass
class VideoRef:
    path: str = ""
    rel_path: str = ""
    fingerprint: dict = field(default_factory=dict)


@dataclass
class Project:
    name: str = "Untitled"
    video: VideoRef = field(default_factory=VideoRef)
    bookmarks: list[Bookmark] = field(default_factory=list)
    annotations: list[Annotation] = field(default_factory=list)
    categories: list[dict] = field(default_factory=lambda: copy.deepcopy(DEFAULT_CATEGORIES))
    players: list[str] = field(default_factory=list)
    home_team: str = ""
    away_team: str = ""
    notes: str = ""
    last_frame: int = 0
    loop_a: Optional[int] = None
    loop_b: Optional[int] = None
    boards: list[Board] = field(default_factory=list)
    id: str = field(default_factory=new_id)

    # ---------------------------------------------------------- lookups
    def annotation(self, aid: str) -> Optional[Annotation]:
        for a in self.annotations:
            if a.id == aid:
                return a
        return None

    def board(self, bid: str) -> Optional[Board]:
        for b in self.boards:
            if b.id == bid:
                return b
        return None

    def bookmark(self, bid: str) -> Optional[Bookmark]:
        for b in self.bookmarks:
            if b.id == bid:
                return b
        return None

    def annotations_at(self, frame: int) -> list[Annotation]:
        return sorted((a for a in self.annotations if a.visible_at(frame)), key=lambda a: a.z)

    def category_color(self, name: str) -> str:
        for c in self.categories:
            if c["name"] == name:
                return c["color"]
        return "#95a5a6"

    def next_z(self) -> int:
        return max((a.z for a in self.annotations), default=0) + 1

    def all_tags(self) -> list[str]:
        return sorted({t for b in self.bookmarks for t in b.tags}, key=str.lower)

    def all_players(self) -> list[str]:
        names = set(self.players) | {p for b in self.bookmarks for p in b.players}
        return sorted(names, key=str.lower)

    def sorted_bookmarks(self) -> list[Bookmark]:
        return sorted(self.bookmarks, key=lambda b: (b.frame, b.created))

    # -------------------------------------------------------- serialisation
    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "id": self.id,
            "video": asdict(self.video),
            "bookmarks": [b.to_dict() for b in self.bookmarks],
            "annotations": [a.to_dict() for a in self.annotations],
            "categories": self.categories,
            "players": self.players,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "notes": self.notes,
            "last_frame": self.last_frame,
            "loop_a": self.loop_a,
            "loop_b": self.loop_b,
            "boards": [b.to_dict() for b in self.boards],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        p = cls()
        p.name = d.get("name", "Untitled")
        p.id = d.get("id") or new_id()
        v = d.get("video") or {}
        p.video = VideoRef(v.get("path", ""), v.get("rel_path", ""), v.get("fingerprint", {}))
        p.bookmarks = [Bookmark.from_dict(b) for b in d.get("bookmarks", [])]
        p.annotations = [Annotation.from_dict(a) for a in d.get("annotations", [])]
        p.categories = d.get("categories") or copy.deepcopy(DEFAULT_CATEGORIES)
        p.players = list(d.get("players", []))
        p.home_team = d.get("home_team", "")
        p.away_team = d.get("away_team", "")
        p.notes = d.get("notes", "")
        p.last_frame = int(d.get("last_frame", 0))
        p.loop_a = d.get("loop_a")
        p.loop_b = d.get("loop_b")
        p.boards = [Board.from_dict(b) for b in d.get("boards", [])]
        return p

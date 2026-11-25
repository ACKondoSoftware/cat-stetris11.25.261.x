import array
import math
import random
import sys
import threading
import time

import pygame

# Game Constants
SCREEN_WIDTH = 800
SCREEN_HEIGHT = 600
BLOCK_SIZE = 20
BOARD_WIDTH = 10
BOARD_HEIGHT = 20
FPS = 60

APP_TITLE = "AC'S Tetris 0.1"

# Colors matching Famicom / NES Tetris palette style
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
YELLOW = (252, 224, 0)
GRAY = (120, 120, 120)
BORDER_COLOR = (150, 150, 150)
BG_BLUE = (15, 60, 200)

MENU_ITEMS = ("1 PLAYER", "EXIT")

SHAPES = {
    "I": [[1, 1, 1, 1]],
    "J": [[1, 0, 0], [1, 1, 1]],
    "L": [[0, 0, 1], [1, 1, 1]],
    "O": [[1, 1], [1, 1]],
    "S": [[0, 1, 1], [1, 1, 0]],
    "T": [[0, 1, 0], [1, 1, 1]],
    "Z": [[1, 1, 0], [0, 1, 1]],
}

SHAPE_COLORS = {
    "I": (240, 20, 20),
    "J": (20, 240, 20),
    "L": (20, 20, 240),
    "O": (240, 240, 20),
    "S": (240, 20, 240),
    "T": (20, 240, 240),
    "Z": (240, 120, 20),
}

# --- Korobeiniki / Tetris Type A (gameplay only) ---
# Full Type A arrangement: A section x2, then B (bridge) section, looped.
# The whole song is pre-rendered ONCE into a single buffer and looped
# gaplessly on a reserved mixer channel, so every note plays its full length
# and timing never drifts (old version synthesized each note live in Python,
# which dropped notes and only covered ~12 s of the melody).
try:
    import numpy as _np
except ImportError:  # pure-Python fallback
    _np = None

SR = 44100
NES_CPU = 1789773.0
TETRIS_BPM = 150
EIGHTH_S = 60.0 / TETRIS_BPM / 2.0  # 0.2 s per eighth at 150 BPM
DUTY_LEAD = 0.5
DUTY_HARM = 0.25
GATE_LEAD = 0.92
GATE_HARM = 0.85
GATE_BASS = 0.96
VOL_LEAD = 0.30
VOL_HARM = 0.12
VOL_BASS = 0.32
VOL_NOISE = 0.05

_NOTE = {
    "R": 0,
    "C2": 36, "D2": 38, "E2": 40, "A2": 45,
    "GS3": 56, "A3": 57, "B3": 59,
    "C4": 60, "D4": 62, "E4": 64, "F4": 65, "G4": 67, "GS4": 68, "A4": 69, "B4": 71,
    "C5": 72, "D5": 74, "E5": 76, "F5": 77, "G5": 79, "GS5": 80, "A5": 81,
}


def _parse_score(rows):
    out = []
    for row in rows:
        name, dur = row.split(":")
        out.append((_NOTE[name], float(dur)))
    return tuple(out)


# Durations in eighth notes; every bar = 8 eighths (4/4).
_SECTION_A = (
    "E5:2", "B4:1", "C5:1", "D5:2", "C5:1", "B4:1",   # 1
    "A4:2", "A4:1", "C5:1", "E5:2", "D5:1", "C5:1",   # 2
    "B4:3", "C5:1", "D5:2", "E5:2",                   # 3
    "C5:2", "A4:2", "A4:2", "R:2",                    # 4
    "D5:3", "F5:1", "A5:2", "G5:1", "F5:1",           # 5
    "E5:3", "C5:1", "E5:2", "D5:1", "C5:1",           # 6
    "B4:2", "B4:1", "C5:1", "D5:2", "E5:2",           # 7
    "C5:2", "A4:2", "A4:2", "R:2",                    # 8
)
_CHORDS_A = ("E", "A", "E", "A", "D", "C", "E", "A")

_SECTION_B = (
    "E5:4", "C5:4",                                   # 1
    "D5:4", "B4:4",                                   # 2
    "C5:4", "A4:4",                                   # 3
    "GS4:4", "B4:2", "R:2",                           # 4
    "E5:4", "C5:4",                                   # 5
    "D5:4", "B4:4",                                   # 6
    "C5:2", "E5:2", "A5:4",                           # 7
    "GS5:8",                                          # 8
)
_CHORDS_B = ("A", "E", "A", "E", "A", "E", "A", "E")

_BASS_ROOT = {"A": "A2", "E": "E2", "D": "D2", "C": "C2"}

# Song form: A A B (then loops)
_FORM = (
    (_SECTION_A, _CHORDS_A),
    (_SECTION_A, _CHORDS_A),
    (_SECTION_B, _CHORDS_B),
)

# Diatonic third below in A (harmonic) minor, by pitch class -> semitones down
_THIRD_BELOW = {9: 4, 11: 4, 0: 3, 2: 3, 4: 4, 5: 3, 7: 3, 8: 4}


def _build_events():
    """Return (events, total_eighths). Event = (start, dur, midi, voice)."""
    events = []
    t = 0.0
    for score, chords in _FORM:
        notes = _parse_score(score)
        assert sum(d for _, d in notes) == 8 * len(chords), "bar length mismatch"
        sec_start = t
        for midi, dur in notes:
            if midi > 0:
                events.append((t, dur, midi, "lead"))
                down = _THIRD_BELOW.get(midi % 12, 3)
                events.append((t, dur, midi - down, "harm"))
            t += dur
        for bar, ch in enumerate(chords):
            root = _NOTE[_BASS_ROOT[ch]]
            for e in range(8):  # NES-style octave-bouncing eighths
                events.append(
                    (sec_start + bar * 8 + e, 1.0, root + (12 if e % 2 else 0), "bass")
                )
            for beat in range(4):
                events.append((sec_start + bar * 8 + beat * 2, 1.0, 0, "noise"))
    return events, t


_PERIOD: dict[int, int] = {}


def _hz(midi: int) -> float:
    """Frequency quantized to the NES APU pulse period table."""
    if midi <= 0:
        return 0.0
    if midi not in _PERIOD:
        f = 440.0 * (2.0 ** ((midi - 69) / 12.0))
        _PERIOD[midi] = max(0, int(round(NES_CPU / (16.0 * f) - 1.0)))
    return NES_CPU / (16.0 * (_PERIOD[midi] + 1))


_VOICE = {
    "lead": (VOL_LEAD, GATE_LEAD, DUTY_LEAD),
    "harm": (VOL_HARM, GATE_HARM, DUTY_HARM),
    "bass": (VOL_BASS, GATE_BASS, 0.0),
    "noise": (VOL_NOISE, 0.15, 0.0),
}


def _render_numpy(events, total, sr):
    n_total = int(round(total * EIGHTH_S * sr))
    mix = _np.zeros(n_total, dtype=_np.float32)
    rng = _np.random.default_rng(1985)
    atk = max(1, int(0.003 * sr))
    rel = max(1, int(0.012 * sr))
    for start, dur, midi, voice in events:
        vol, gate, duty = _VOICE[voice]
        s0 = int(round(start * EIGHTH_S * sr))
        n = int(dur * EIGHTH_S * sr * gate)
        n = min(n, n_total - s0)
        if n <= 0:
            continue
        if voice == "noise":
            w = rng.uniform(-1.0, 1.0, n).astype(_np.float32)
            w *= _np.exp(-_np.arange(n) / (0.012 * sr)).astype(_np.float32)
        else:
            ph = (_np.arange(n) * (_hz(midi) / sr)) % 1.0
            if voice == "bass":  # 4-bit stepped triangle like the NES APU
                tri = 1.0 - 4.0 * _np.abs(ph - 0.5)
                w = (_np.round((tri + 1.0) * 7.5) / 7.5 - 1.0).astype(_np.float32)
            else:
                w = _np.where(ph < duty, 1.0, -1.0).astype(_np.float32)
            env = _np.ones(n, dtype=_np.float32)
            a = min(atk, n)
            env[:a] = _np.linspace(0.0, 1.0, a, dtype=_np.float32)
            r = min(rel, n)
            env[n - r:] *= _np.linspace(1.0, 0.0, r, dtype=_np.float32)
            w *= env
        mix[s0:s0 + n] += w * vol
    peak = float(_np.max(_np.abs(mix))) or 1.0
    mix *= min(1.0, 0.95 / peak)
    return (mix * 32767.0).astype(_np.int16)


def _render_pure(events, total, sr):
    n_total = int(round(total * EIGHTH_S * sr))
    mix = [0.0] * n_total
    rnd = random.Random(1985)
    atk = max(1, int(0.003 * sr))
    rel = max(1, int(0.012 * sr))
    for start, dur, midi, voice in events:
        vol, gate, duty = _VOICE[voice]
        s0 = int(round(start * EIGHTH_S * sr))
        n = min(int(dur * EIGHTH_S * sr * gate), n_total - s0)
        if n <= 0:
            continue
        if voice == "noise":
            k = 1.0 / (0.012 * sr)
            for i in range(n):
                mix[s0 + i] += vol * math.exp(-i * k) * (rnd.random() * 2.0 - 1.0)
            continue
        inc = _hz(midi) / sr
        ph = 0.0
        for i in range(n):
            env = 1.0
            if i < atk:
                env = i / atk
            if i > n - rel:
                env *= (n - i) / rel
            if voice == "bass":
                tri = 1.0 - 4.0 * abs(ph - 0.5)
                w = round((tri + 1.0) * 7.5) / 7.5 - 1.0
            else:
                w = 1.0 if ph < duty else -1.0
            mix[s0 + i] += w * env * vol
            ph += inc
            if ph >= 1.0:
                ph -= 1.0
    peak = max(abs(v) for v in mix) or 1.0
    g = min(1.0, 0.95 / peak) * 32767.0
    return array.array("h", (int(v * g) for v in mix))


class TetrisMusic:
    """Loops the full Korobeiniki (Type A) during play; silent on main menu."""

    def __init__(self) -> None:
        self.enabled = True
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._pcm = None
        self._sound: pygame.mixer.Sound | None = None
        self._channel: pygame.mixer.Channel | None = None
        self._want_play = False
        self._render_thread: threading.Thread | None = None

    def prerender(self) -> None:
        """Render the song in the background (call once after mixer init)."""
        if self._render_thread or not pygame.mixer.get_init():
            return
        freq, _size, chans = pygame.mixer.get_init()
        self._render_thread = threading.Thread(
            target=self._render, args=(freq, chans), daemon=True
        )
        self._render_thread.start()

    def _render(self, freq: int, chans: int) -> None:
        events, total = _build_events()
        if _np is not None:
            pcm = _render_numpy(events, total, freq)
            if chans == 2:
                pcm = _np.repeat(pcm, 2)
            data = pcm.tobytes()
        else:
            pcm = _render_pure(events, total, freq)
            if chans == 2:
                st = array.array("h", [0]) * (len(pcm) * 2)
                st[0::2] = pcm
                st[1::2] = pcm
                pcm = st
            data = pcm.tobytes()
        self._pcm = data
        self._ready.set()
        with self._lock:
            if self._want_play:
                self._play_locked()

    def _play_locked(self) -> None:
        if not self.enabled or self._pcm is None:
            return
        if self._sound is None:
            self._sound = pygame.mixer.Sound(buffer=self._pcm)
        if self._channel is None:
            pygame.mixer.set_reserved(1)
            self._channel = pygame.mixer.Channel(0)
        self._channel.play(self._sound, loops=-1)

    def start(self) -> None:
        if not pygame.mixer.get_init():
            return
        self.prerender()
        with self._lock:
            self._want_play = True
            if self._ready.is_set():
                self._play_locked()

    def stop(self) -> None:
        with self._lock:
            self._want_play = False
            if self._channel is not None:
                self._channel.stop()


_game_music: TetrisMusic | None = None


def _get_music() -> TetrisMusic:
    global _game_music
    if _game_music is None:
        _game_music = TetrisMusic()
    return _game_music


def _draw_block_letter(screen, x, y, letter, color, size=10):
    """Tiny 5x5 block font for NES-style logo accents."""
    glyphs = {
        "T": ["11111", "00100", "00100", "00100", "00100"],
        "E": ["11111", "10000", "11110", "10000", "11111"],
        "R": ["11110", "10001", "11110", "10100", "10001"],
        "I": ["11111", "00100", "00100", "00100", "11111"],
        "S": ["01111", "10000", "01110", "00001", "11110"],
        "A": ["01110", "10001", "11111", "10001", "10001"],
        "C": ["01111", "10000", "10000", "10000", "01111"],
        "'": ["010", "100", "000", "000", "000"],
        "U": ["10001", "10001", "10001", "10001", "01110"],
        "L": ["10000", "10000", "10000", "10000", "11111"],
        "!": ["00100", "00100", "00100", "00000", "00100"],
    }
    pat = glyphs.get(letter.upper())
    if not pat:
        return
    for r, row in enumerate(pat):
        for c, ch in enumerate(row):
            if ch == "1":
                pygame.draw.rect(
                    screen, color, (x + c * size, y + r * size, size - 1, size - 1)
                )


def run_menu(screen, clock):
    """NES-style title menu centered on a black screen."""
    pygame.key.set_repeat(0, 0)
    title_font = pygame.font.SysFont("Courier", 52, bold=True)
    sub_font = pygame.font.SysFont("Courier", 22, bold=True)
    menu_font = pygame.font.SysFont("Courier", 32, bold=True)
    hint_font = pygame.font.SysFont("Courier", 20)

    selected = 0
    blink_on = True
    blink_ms = 0

    cx = SCREEN_WIDTH // 2
    cy = SCREEN_HEIGHT // 2

    while True:
        dt = clock.tick(FPS)
        blink_ms += dt
        if blink_ms >= 450:
            blink_ms = 0
            blink_on = not blink_on

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return "quit"
            if event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_ESCAPE,):
                    return "quit"
                if event.key in (pygame.K_UP, pygame.K_w):
                    selected = (selected - 1) % len(MENU_ITEMS)
                elif event.key in (pygame.K_DOWN, pygame.K_s):
                    selected = (selected + 1) % len(MENU_ITEMS)
                elif event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    return "play" if selected == 0 else "quit"

        screen.fill(BLACK)

        # ULTRA!TETRIS block logo, cycling through the piece colors
        logo_y = cy - 150
        letters = "ULTRA!TETRIS"
        palette = [SHAPE_COLORS[k] for k in ("I", "O", "T", "J", "L", "S", "Z")]
        cell = 8
        step = 6 * cell
        lx = cx - (len(letters) * step - cell) // 2
        for i, ch in enumerate(letters):
            color = WHITE if ch == "!" else palette[i % len(palette)]
            _draw_block_letter(screen, lx + i * step, logo_y, ch, color, cell)

        # Main title — centered
        title = title_font.render("AC'S Tetris 0.1", True, WHITE)
        screen.blit(title, title.get_rect(center=(cx, cy - 35)))

        sub = sub_font.render("(C) AC HOLDINGS", True, GRAY)
        screen.blit(sub, sub.get_rect(center=(cx, cy + 5)))

        # Menu block (NES type-A / type-B layout, centered)
        menu_top = cy + 55
        for i, label in enumerate(MENU_ITEMS):
            is_sel = i == selected
            color = YELLOW if is_sel else WHITE
            cursor = "► " if is_sel else "   "
            text = menu_font.render(cursor + label, True, color)
            screen.blit(text, text.get_rect(center=(cx, menu_top + i * 44)))

        if blink_on:
            hint = hint_font.render("PRESS ENTER", True, WHITE)
            screen.blit(hint, hint.get_rect(center=(cx, SCREEN_HEIGHT - 48)))

        nav = hint_font.render("↑↓ SELECT   ENTER START", True, GRAY)
        screen.blit(nav, nav.get_rect(center=(cx, SCREEN_HEIGHT - 22)))

        pygame.display.flip()


def spawn_x(shape_type: str) -> int:
    """Center piece in the 10-wide well (fixes I/O misalignment at spawn)."""
    w = max(len(row) for row in SHAPES[shape_type])
    return (BOARD_WIDTH - w) // 2


class Tetramino:
    def __init__(self, x, y, shape_type):
        self.x = x
        self.y = y
        self.type = shape_type
        self.shape = [row[:] for row in SHAPES[shape_type]]
        self.color = SHAPE_COLORS[shape_type]

    def rotate(self):
        self.shape = [list(col) for col in zip(*self.shape[::-1])]

    def rotated_shape(self):
        return [list(col) for col in zip(*self.shape[::-1])]


class TetrisFamicom:
    def __init__(self, screen, clock):
        self.screen = screen
        self.clock = clock

        self.board = [[None for _ in range(BOARD_WIDTH)] for _ in range(BOARD_HEIGHT)]

        self.score = 0
        self.stage = 0
        self.lines_target = 25

        self.bag: list[str] = []
        self.game_over = False
        self.curtain_rows = 0  # NES-style top-down fill after topping out
        self.curtain_ms = 0
        self.won = False

        self.fall_time = 0
        self.fall_speed = 500

        self.next_piece = self.new_piece()
        self.current_piece = self.new_piece()
        if self.check_collision(self.current_piece):
            self.top_out()

    def _pull_shape(self) -> str:
        if not self.bag:
            self.bag = list(SHAPES.keys())
            random.shuffle(self.bag)
        return self.bag.pop()

    # FIX: Cleaned type hinting syntax to support native implementations seamlessly
    def new_piece(self, shape_type: str = None) -> Tetramino:
        st = shape_type or self._pull_shape()
        return Tetramino(spawn_x(st), 0, st)

    def check_collision(self, piece, offset_x=0, offset_y=0, shape=None):
        shape = shape or piece.shape
        for r, row in enumerate(shape):
            for c, val in enumerate(row):
                if not val:
                    continue
                new_x = piece.x + c + offset_x
                new_y = piece.y + r + offset_y
                if new_x < 0 or new_x >= BOARD_WIDTH or new_y >= BOARD_HEIGHT:
                    return True
                if new_y >= 0 and self.board[new_y][new_x] is not None:
                    return True
        return False

    def lock_piece(self):
        if self.game_over:
            return

        locked_above_top = False
        for r, row in enumerate(self.current_piece.shape):
            for c, val in enumerate(row):
                if not val:
                    continue
                by = self.current_piece.y + r
                bx = self.current_piece.x + c
                if by < 0:
                    locked_above_top = True
                    continue
                if 0 <= by < BOARD_HEIGHT and 0 <= bx < BOARD_WIDTH:
                    self.board[by][bx] = self.current_piece.color

        # Lock-out: piece came to rest sticking out of the top of the well.
        if locked_above_top:
            self.top_out()
            return

        self.clear_lines()
        if self.game_over:
            return

        self.current_piece = self.next_piece
        self.next_piece = self.new_piece()
        self.fall_time = 0
        # Block-out: the stack reaches the spawn area, so the new piece can't enter.
        if self.check_collision(self.current_piece):
            self.top_out()

    def top_out(self):
        """Screen is full: end the run immediately. No board wipe, no continue."""
        if self.game_over:
            return
        self.game_over = True
        self.won = False
        self.fall_time = 0

    def clear_lines(self):
        cleared = 0
        r = BOARD_HEIGHT - 1
        while r >= 0:
            if all(cell is not None for cell in self.board[r]):
                del self.board[r]
                self.board.insert(0, [None for _ in range(BOARD_WIDTH)])
                cleared += 1
            else:
                r -= 1

        if cleared <= 0:
            return

        self.score += (cleared ** 2) * 100 * (self.stage + 1)
        self.lines_target -= cleared

        while self.lines_target <= 0:
            self.stage += 1
            self.lines_target += 25
            self.fall_speed = max(80, self.fall_speed - 40)
            if self.stage > 9:
                self.won = True
                self.game_over = True
                return

    def move(self, dx):
        if self.game_over:
            return
        if not self.check_collision(self.current_piece, offset_x=dx):
            self.current_piece.x += dx

    def try_move_down(self) -> bool:
        """Move down one row; return True if moved."""
        if self.game_over:
            return False
        if not self.check_collision(self.current_piece, offset_y=1):
            self.current_piece.y += 1
            return True
        return False

    def soft_drop(self):
        if self.try_move_down():
            self.score += 1
        else:
            self.lock_piece()

    def gravity_step(self):
        if not self.try_move_down():
            self.lock_piece()

    def hard_drop(self):
        if self.game_over:
            return
        while self.try_move_down():
            self.score += 2
        self.lock_piece()

    def rotate_piece(self):
        if self.game_over:
            return
        rotated = self.current_piece.rotated_shape()
        for kick in (0, -1, 1, -2, 2):
            if not self.check_collision(
                self.current_piece, offset_x=kick, shape=rotated
            ):
                self.current_piece.shape = [row[:] for row in rotated]
                if kick:
                    self.current_piece.x += kick
                return

    def draw_hud(self):
        font = pygame.font.SysFont("Courier", 28, bold=True)

        labels = [
            f"SCORE  {self.score:06d}",
            f"STAGE  {self.stage}",
            f"TO GO  {self.lines_target:02d}",
        ]

        for i, label in enumerate(labels):
            text_surface = font.render(label, True, WHITE)
            self.screen.blit(text_surface, (50, 150 + i * 50))

        hint = pygame.font.SysFont("Courier", 18).render("ESC  MENU", True, GRAY)
        self.screen.blit(hint, (50, 55))

    def draw_piece(self, piece, offset_x, offset_y):
        for r, row in enumerate(piece.shape):
            for c, val in enumerate(row):
                if not val:
                    continue
                px = piece.x + c
                py = piece.y + r
                if py < 0 or px < 0 or px >= BOARD_WIDTH:
                    continue
                pygame.draw.rect(
                    self.screen,
                    piece.color,
                    (
                        offset_x + px * BLOCK_SIZE,
                        offset_y + py * BLOCK_SIZE,
                        BLOCK_SIZE - 1,
                        BLOCK_SIZE - 1,
                    ),
                )

    def draw_board(self):
        board_offset_x = 300
        board_offset_y = 100

        pygame.draw.rect(
            self.screen,
            BORDER_COLOR,
            (
                board_offset_x - 4,
                board_offset_y - 4,
                BOARD_WIDTH * BLOCK_SIZE + 8,
                BOARD_HEIGHT * BLOCK_SIZE + 8,
            ),
            4,
        )
        pygame.draw.rect(
            self.screen,
            BLACK,
            (
                board_offset_x,
                board_offset_y,
                BOARD_WIDTH * BLOCK_SIZE,
                BOARD_HEIGHT * BLOCK_SIZE,
            ),
        )

        for r, row in enumerate(self.board):
            for c, color in enumerate(row):
                if color:
                    pygame.draw.rect(
                        self.screen,
                        color,
                        (
                            board_offset_x + c * BLOCK_SIZE,
                            board_offset_y + r * BLOCK_SIZE,
                            BLOCK_SIZE - 1,
                            BLOCK_SIZE - 1,
                        ),
                    )

        if not self.game_over:
            self.draw_piece(self.current_piece, board_offset_x, board_offset_y)

        next_box_x = 550
        next_box_y = 250
        font = pygame.font.SysFont("Courier", 24, bold=True)
        next_surface = font.render("NEXT", True, WHITE)
        self.screen.blit(next_surface, (next_box_x, next_box_y - 35))
        pygame.draw.rect(
            self.screen,
            BORDER_COLOR,
            (next_box_x - 4, next_box_y - 4, 5 * BLOCK_SIZE + 8, 4 * BLOCK_SIZE + 8),
            4,
        )
        pygame.draw.rect(
            self.screen,
            BLACK,
            (next_box_x, next_box_y, 5 * BLOCK_SIZE, 4 * BLOCK_SIZE),
        )

        pw = max(len(row) for row in self.next_piece.shape)
        ph = len(self.next_piece.shape)
        ox = (5 - pw) // 2
        oy = (4 - ph) // 2
        for r, row in enumerate(self.next_piece.shape):
            for c, val in enumerate(row):
                if val:
                    pygame.draw.rect(
                        self.screen,
                        self.next_piece.color,
                        (
                            next_box_x + (ox + c) * BLOCK_SIZE,
                            next_box_y + (oy + r) * BLOCK_SIZE,
                            BLOCK_SIZE - 1,
                            BLOCK_SIZE - 1,
                        ),
                    )

    def update_curtain(self, dt):
        """NES Tetris game over: the well fills with gray, top to bottom."""
        if self.won or self.curtain_rows >= BOARD_HEIGHT:
            return
        self.curtain_ms += dt
        while self.curtain_ms >= 50 and self.curtain_rows < BOARD_HEIGHT:
            self.curtain_ms -= 50
            self.curtain_rows += 1

    def curtain_done(self):
        return self.won or self.curtain_rows >= BOARD_HEIGHT

    def draw_curtain(self):
        ox, oy = 300, 100
        for r in range(self.curtain_rows):
            for c in range(BOARD_WIDTH):
                x = ox + c * BLOCK_SIZE
                y = oy + r * BLOCK_SIZE
                pygame.draw.rect(self.screen, GRAY, (x, y, BLOCK_SIZE - 1, BLOCK_SIZE - 1))
                pygame.draw.rect(self.screen, WHITE, (x, y, BLOCK_SIZE - 1, 3))

    def draw_game_over(self):
        font = pygame.font.SysFont("Courier", 36, bold=True)
        headline = "YOU WIN!" if self.won else "GAME OVER"
        msg = font.render(headline, True, WHITE)
        score = pygame.font.SysFont("Courier", 28, bold=True).render(
            f"SCORE {self.score:06d}", True, WHITE
        )
        hint = pygame.font.SysFont("Courier", 22).render(
            "ESC menu   ENTER back", True, WHITE
        )
        self.screen.blit(msg, msg.get_rect(center=(SCREEN_WIDTH // 2, 260)))
        self.screen.blit(score, score.get_rect(center=(SCREEN_WIDTH // 2, 310)))
        self.screen.blit(hint, hint.get_rect(center=(SCREEN_WIDTH // 2, 360)))

    def run(self):
        pygame.key.set_repeat(140, 40)
        pygame.event.clear()
        music = _get_music()
        music.start()
        try:
            return self._run_loop()
        finally:
            music.stop()

    def _run_loop(self):
        esc_down = False
        music_stopped = False
        while True:
            # Game over / win: cut the OST once, keep showing the end screen.
            if self.game_over and not music_stopped:
                _get_music().stop()
                music_stopped = True
            dt = self.clock.tick(FPS)
            self.screen.fill(BG_BLUE)

            if pygame.key.get_pressed()[pygame.K_ESCAPE] and not esc_down:
                return "menu"
            esc_down = pygame.key.get_pressed()[pygame.K_ESCAPE]

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    return "quit"
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        return "menu"
                    if self.game_over:
                        if self.curtain_done() and event.key in (
                            pygame.K_RETURN,
                            pygame.K_SPACE,
                        ):
                            return "menu"
                        continue
                    if event.key == pygame.K_LEFT:
                        self.move(-1)
                    elif event.key == pygame.K_RIGHT:
                        self.move(1)
                    elif event.key == pygame.K_DOWN:
                        self.soft_drop()
                    elif event.key == pygame.K_UP:
                        self.rotate_piece()
                    elif event.key == pygame.K_SPACE:
                        self.hard_drop()

            if not self.game_over:
                self.fall_time += dt
                if self.fall_time >= self.fall_speed:
                    self.gravity_step()
                    self.fall_time = 0

            if self.game_over:
                self.update_curtain(dt)

            self.draw_hud()
            self.draw_board()
            if self.game_over:
                self.draw_curtain()
                if self.curtain_done():
                    self.draw_game_over()
            pygame.display.flip()


def main():
    pygame.mixer.pre_init(SR, -16, 1, 512)
    pygame.init()
    try:
        pygame.mixer.init()
    except pygame.error:
        pass
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(APP_TITLE)
    _get_music().prerender()  # render song while the title menu is up
    clock = pygame.time.Clock()
    try:
        while True:
            if run_menu(screen, clock) != "play":
                break
            pygame.event.clear()
            result = TetrisFamicom(screen, clock).run()
            pygame.key.set_repeat(0, 0)
            pygame.event.clear()
            if result == "quit":
                break
            # "menu" or anything else → back to title screen
            continue
    finally:
        _get_music().stop()
        pygame.quit()


if __name__ == "__main__":
    main()
    sys.exit(0)

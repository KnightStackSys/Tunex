"""Resolve 4-player Teams SAN/PGN4 moves against IronPhoenix's legal moves.

SAN is deliberately interpreted using the native engine's current FEN4 and
legal move list: python-chess and orthodox 8x8 move rules do not apply to 4PC.
Conversion runs once per opening game, not in the search hot path.
"""
from __future__ import annotations

import re
from typing import Iterable

_SQUARE = r"[a-n](?:1[0-4]|[1-9])"
_UCI = re.compile(rf"(?P<from>{_SQUARE})(?P<to>{_SQUARE})(?P<promo>[qrbn])?", re.I)
_LONG = re.compile(
    rf"(?P<piece>[KQRBNP])?(?P<from>{_SQUARE})"
    rf"(?P<separator>[-x:])(?P<captured>[KQRBNP])?"
    rf"(?P<to>{_SQUARE})(?:=?(?P<promo>[QRBN]))?", re.I
)
_SAN = re.compile(
    rf"(?P<piece>[KQRBNP])?"
    rf"(?P<source>{_SQUARE}|[a-n]|1[0-4]|[1-9])?"
    rf"(?P<capture>x)?(?P<to>{_SQUARE})"
    rf"(?:=(?P<promo>[QRBN]))?", re.I
)
_CASTLE = {"O-O": 1, "O-O-O": -1}
_CASTLE_KINGSIDE = {"R": (2, 0), "B": (0, -2), "Y": (-2, 0), "G": (0, 2)}
_FILES = "abcdefghijklmn"


def coordinate_move(token: str) -> str | None:
    """Return normalized UCI move, or None for a SAN/long-algebraic token."""
    match = _UCI.fullmatch(token.strip())
    return match.group(0).lower() if match else None


def _square_parts(square: str) -> tuple[int, int]:
    return _FILES.index(square[0]), int(square[1:])


def fen4_board(fen: str) -> tuple[str, dict[str, str]]:
    """Parse pieces from the native FEN4 snapshot (not orthodox FEN)."""
    if not fen or fen[0].upper() not in "RBYG":
        raise ValueError("invalid FEN4 turn in opening conversion")
    ranks = fen.rsplit("-", 1)[-1].split("/")
    if len(ranks) != 14:
        raise ValueError("invalid FEN4 board in opening conversion (expected 14 ranks)")
    board: dict[str, str] = {}
    for rank_index, rank in enumerate(ranks):
        file = 0
        for token in rank.split(","):
            token = token.strip()
            if token.isdecimal() and 1 <= int(token) <= 14:
                file += int(token)
            elif token.lower() == "x":
                file += 1
            elif re.fullmatch(r"[rbyg][pnbrqk]", token, re.I):
                if file >= 14:
                    raise ValueError("invalid FEN4 rank width")
                square = f"{_FILES[file]}{14 - rank_index}"
                board[square] = token[0].lower() + token[1].upper()
                file += 1
            else:
                raise ValueError(f"invalid FEN4 board token {token!r}")
            if file > 14:
                raise ValueError("invalid FEN4 rank width")
        if file != 14:
            raise ValueError("invalid FEN4 rank width")
    return fen[0].upper(), board


def _is_capture(board: dict[str, str], piece_type: str, src: str, dst: str) -> bool:
    if dst in board:
        return True
    # A diagonal pawn move to an unoccupied square can only be en passant,
    # since all four pawn colors move orthogonally on quiet moves.
    sf, sr = _square_parts(src)
    df, dr = _square_parts(dst)
    return piece_type == "P" and abs(sf - df) == 1 and abs(sr - dr) == 1


def _strip_suffixes(token: str) -> str:
    # Annotations/check suffixes are accepted, but do not determine legality:
    # IronPhoenix's legal move list is the authority.
    return token.strip().rstrip("+#?!")


def san_to_uci(token: str, fen: str, legal_moves: Iterable[str]) -> str:
    """Convert one 4PC SAN token to a unique legal UCI move.

    Supports KQRBN, pawn SAN (including captures), source file/rank/square
    disambiguation, =Q/R/B/N, O-O / O-O-O and PGN4 long algebraic.
    """
    normalized = _strip_suffixes(token)
    turn, board = fen4_board(fen)
    legal = [move.lower() for move in legal_moves]
    castle = _CASTLE.get(normalized.replace("0", "O").upper())
    long_match = _LONG.fullmatch(normalized)
    san_match = _SAN.fullmatch(normalized)

    if not castle and not long_match and not san_match:
        raise ValueError(f"unsupported SAN opening move {token!r}")

    if castle:
        king_dir = _CASTLE_KINGSIDE[turn]
        delta = king_dir if castle == 1 else (-king_dir[0], -king_dir[1])
        candidates = []
        for move in legal:
            match = _UCI.fullmatch(move)
            if not match:
                continue
            src, dst = match["from"], match["to"]
            piece = board.get(src)
            if piece != turn.lower() + "K":
                continue
            sf, sr = _square_parts(src)
            df, dr = _square_parts(dst)
            if (df - sf, dr - sr) == delta:
                candidates.append(move)
    else:
        match = long_match or san_match
        assert match is not None
        source_filter = match["from"].lower() if long_match else (match["source"] or "").lower()
        # A PGN4 long-algebraic coordinate can omit the piece letter.
        # Unlike SAN pawn notation, its source square tells us the real type.
        expected_piece = ((match["piece"] or
                           (board.get(source_filter, "")[1:] if long_match else "P"))
                          .upper())
        dest = match["to"].lower()
        promo = (match["promo"] or "").lower()
        capture_flag = bool(match["separator"] == "x") if long_match else bool(match["capture"])
        candidates = []
        for move in legal:
            candidate = _UCI.fullmatch(move)
            if candidate is None or candidate["to"] != dest:
                continue
            src = candidate["from"]
            piece = board.get(src)
            if piece != turn.lower() + expected_piece:
                continue
            if (candidate["promo"] or "") != promo:
                continue
            if source_filter:
                # A single letter means origin file; digits mean origin rank;
                # a complete square is unambiguous long algebraic.
                if len(source_filter) > 1 and source_filter[0] in _FILES:
                    matches_source = src == source_filter
                elif source_filter[0] in _FILES:
                    matches_source = src[0] == source_filter
                else:
                    matches_source = src[1:] == source_filter
                if not matches_source:
                    continue
            if _is_capture(board, expected_piece, src, dest) != capture_flag:
                continue
            if long_match and match["captured"]:
                victim = board.get(dest)
                if victim is None or victim[1].upper() != match["captured"].upper():
                    continue
            candidates.append(move)

    if not candidates:
        raise ValueError(f"illegal SAN opening move {token!r} for {turn} to move")
    if len(candidates) > 1:
        raise ValueError(
            f"ambiguous SAN opening move {token!r} for {turn} to move: "
            + ", ".join(candidates)
            + "; add a source file/rank/square"
        )
    return candidates[0]

"""The lyric's own structure: its "[Verse 1]" / "[Chorus]" tags and the blank lines between its stanzas. Standard
library only (analyze_audio.py, song_kit.py and the tests use it).

walk() reads a lyric (plain text or LRC) line by line: every sung line gets its "part" (the section tag above it, as
written; None before the first one) and its "stanza" (a blank line, an empty LRC line or a section tag starts the next
one). analyze_audio.py keeps both on each lyric line; song_kit.py then names the sections from the tags when the lyric
is tagged (tagged()), and otherwise works the sections out from the sound and the lyric's repeats, with each stanza
break as strong evidence of a boundary. Tags that are no section ("[Male vocal]", "[Spoken]") are skipped.
"""
import re

TIME = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
LRC_META = re.compile(r"^(ar|ti|al|au|by|re|ve|length|offset|tool|lang|la|id)\s*:", re.I)    # LRC header tags
KINDS = (                                   # the tag's words -> the section label the studio uses; the first match wins
    (r"\bpre[\s-]*(chorus|hook)\b|\bbuild([\s-]*up)?\b|\blift\b", "pre-chorus"),
    (r"\bpost[\s-]*(chorus|hook)\b", "post-chorus"),
    (r"\bchorus\b|\bhook\b|\bdrop\b", "chorus"),
    (r"\brefrain\b", "refrain"),
    (r"\bverse\b|\brap\b", "verse"),
    (r"\bbridge\b|\bmiddle[\s-]*(8|eight)\b", "bridge"),
    (r"\bintro(duction)?\b", "intro"),
    (r"\boutro\b|\bcoda\b|\bend(ing)?\b|\bfade([\s-]*out)?\b", "outro"),
    (r"\binstrumental\b|\binterlude\b|\bsolo\b", "instrumental"),
    (r"\bbreak([\s-]*down)?\b", "break"),
)


def kind(tag):
    """The section label for a tag ("Final Chorus" -> chorus, "Breakdown" -> break), None when it names no section."""
    t = str(tag or "").lower()
    for pat, label in KINDS:
        if re.search(pat, t):
            return label
    return None


def walk(text):
    """[{text, part, stanza, stamps}] for every sung line of a lyric, in the file's order. text = the line without times
    or tags, part = the last section tag above it (None before the first), stanza = its stanza's number, stamps = its
    LRC times (none in plain text). Comment lines (#) are skipped."""
    out, part, st, fresh = [], None, 0, False
    for raw in str(text or "").splitlines():
        s = raw.strip()
        if s.startswith("#"):
            continue
        stamps = [int(m) * 60 + float(x) for m, x in TIME.findall(s)]
        body = TIME.sub("", s).strip()
        m = re.match(r"^\[([^\]]*)\]\s*(.*)$", body)
        if m and not LRC_META.match(m.group(1).strip()):
            tag = re.sub(r"\s+", " ", m.group(1)).strip()
            if kind(tag):
                part, fresh = tag, True
            body = m.group(2)
        body = re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", re.sub(r"\[[^\]]*\]", "", body))).strip()
        if not body:
            if not s or stamps or (m and kind(m.group(1))):       # a blank line, an empty LRC line: the stanza ends
                fresh = True
            continue
        if fresh and out:
            st += 1
        fresh = False
        out.append({"text": body, "part": part, "stanza": st, "stamps": stamps})
    return out


def lrc_map(text):
    """{LRC time (rounded to ms): (part, stanza)} for every timed line of an LRC lyric."""
    return {round(t, 3): (x["part"], x["stanza"]) for x in walk(text) for t in x["stamps"]}


def fields(part, stanza):
    """The keys a lyric line carries for its place in the lyric (none when it has no place: a repeat Whisper heard)."""
    return {**({"part": part} if part else {}), **({"stanza": stanza} if stanza is not None else {})}


def tagged(lines):
    """True when the lyric names its own sections: its first line is under a section tag and there are two parts or
    more. A lyric that tags only some of its sections (an untagged opening) is not: its tags can't say where an
    untagged part starts."""
    own = [x for x in lines if x.get("stanza") is not None]
    return bool(own) and bool(own[0].get("part")) and len({x.get("part") for x in own}) >= 2

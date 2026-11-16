"""Rule-based L1/L2 labels from the scan's evidence (evidence/evidence.sqlite3), instead of an LLM pass.

Each hit is read as typed claims (claims.py); a user's claims are counted per distinct comment, and decide()
labels the user "L1" or "L2" only when the claims agree, "ambiguous" otherwise. The label comes with its basis
(which kind of evidence decided it), so that later steps can choose how strict to be. See notes.txt.

Run as a script, it labels every user in the evidence database and writes (gitignored):
  lists/labels.tsv   author, label, basis for all users
  lists/L1.txt       user names labelled L1, one per line
  lists/L2.txt       user names labelled L2
"""
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from itertools import groupby
from multiprocessing import Pool
from pathlib import Path

import claims
import places

HERE = Path(__file__).resolve().parent

# Subreddits whose flairs name the user's country, state or region.
PLACE_FLAIR_SUBS = {"europe", "askeurope", "polandball", "canada", "unitedkingdom", "askanamerican", "politics",
                    "unemployment", "de", "germany", "denmark", "finland", "sweden", "norway", "thenetherlands",
                    "ireland", "australia", "newzealand", "askuk", "ukpolitics", "canadapolitics", "casualuk",
                    "anime_titties", "askarussian", "ich_iel", "france", "italy", "spain", "poland", "portugal",
                    "brasil", "mexico", "argentina", "india", "philippines"}
FLAG_LANG = {"🇺🇸": "english", "🇺🇲": "english", "🇬🇧": "english", "🇨🇦": "english", "🇦🇺": "english", "🇳🇿": "english",
             "🇮🇪": "english", "🇩🇪": "german", "🇦🇹": "german", "🇫🇷": "french", "🇪🇸": "spanish", "🇲🇽": "spanish",
             "🇮🇹": "italian", "🇵🇹": "portuguese", "🇧🇷": "portuguese", "🇷🇺": "russian", "🇯🇵": "japanese",
             "🇨🇳": "chinese", "🇰🇷": "korean", "🇳🇱": "dutch", "🇸🇪": "swedish", "🇳🇴": "norwegian", "🇩🇰": "danish",
             "🇫🇮": "finnish", "🇵🇱": "polish", "🇨🇿": "czech", "🇭🇺": "hungarian", "🇷🇴": "romanian", "🇹🇷": "turkish",
             "🇬🇷": "greek", "🇮🇱": "hebrew", "🇺🇦": "ukrainian", "🇧🇬": "bulgarian", "🇭🇷": "croatian", "🇷🇸": "serbian"}
CODE_LANG = {"en": "english", "eng": "english", "de": "german", "ger": "german", "fr": "french", "es": "spanish",
             "esp": "spanish", "it": "italian", "pt": "portuguese", "ru": "russian", "ja": "japanese", "jp": "japanese",
             "zh": "chinese", "ko": "korean", "nl": "dutch", "sv": "swedish", "no": "norwegian", "nb": "norwegian",
             "da": "danish", "fi": "finnish", "pl": "polish", "cs": "czech", "hu": "hungarian", "ro": "romanian",
             "tr": "turkish", "el": "greek", "he": "hebrew", "uk": "ukrainian", "ar": "arabic", "hr": "croatian"}
LANG_TOKEN = r"(?:[A-Za-z]+|[\U0001F1E6-\U0001F1FF]{2})"
FLAIR_NATIVE = re.compile(r"(?i)(?:^|[\s|,/;(])(?P<a>" + LANG_TOKEN + r")\s*[:\-(]?\s*(?:N|native|L1)\b\)?"
                          r"|\b(?:N|native|L1)\s*[:\-]\s*(?P<b>" + LANG_TOKEN + r")")


def flair_language(token: str) -> str | None:
    t = token.strip()
    return FLAG_LANG.get(t) or CODE_LANG.get(t.lower()) or claims.language(t)


def flair_claims(subreddit: str, flair: str) -> list[tuple[str, str]]:
    sub = subreddit.lower()
    if sub in PLACE_FLAIR_SUBS:
        text = re.sub(r":[\w-]+:", " ", flair)
        text = re.sub(r"\s+", " ", re.sub(r"[^\w\s'.-]", " ", text)).strip()
        if len(text.split()) <= 5:
            found = places.locate(text)
            if found and found[2] != "AMB":
                return [("flair_place", f"{found[2]}:{found[1]}")]
    if claims.LANGUAGE_SUBS.search(subreddit):
        out = []
        for m in FLAIR_NATIVE.finditer(flair):
            lang = flair_language(m["a"] or m["b"])
            if lang:
                out.append(("en_l1", "flair") if lang == "english" else ("other_l1", lang))
        return out
    return []


def summarise(user_claims: list[tuple[str, str, str]]) -> dict:
    """Counts of distinct comments (or flairs) per claim kind, and per kind and place class."""
    seen = defaultdict(set)
    for kind, detail, source in user_claims:
        seen[kind].add(source)
        if kind in ("origin", "nation", "residence", "flair_place"):
            seen[f"{kind}:{detail.split(':')[0]}"].add(source)
            seen[f"{kind}@{detail}"].add(source)
    return {k: len(v) for k, v in seen.items()}


def decide(n: dict) -> tuple[str, str]:
    """(label, basis) from the counts of summarise()."""
    g = lambda *keys: sum(n.get(k, 0) for k in keys)
    l1 = g("en_l1", "mono")
    l2 = g("en_l2")
    l2_unspec = g("nn_unspec")
    other = g("other_l1")
    eng_origin = g("origin:ENG", "nation:ENG")
    non_origin = g("origin:NON")
    non_nation = g("nation:NON")
    out_origin = g("origin:OUT", "nation:OUT")
    eng_res = g("residence:ENG", "flair_place:ENG")
    non_res = g("residence:NON", "flair_place:NON")

    # 1. Explicit statements about English. "I'm not a native speaker" without a language does not count against
    #    an explicit English L1: it is mostly about a language the user learns.
    if l1 and not l2:
        return "L1", "explicit"
    if (l2 or l2_unspec) and not l1:
        if g("child_en") or (l2 and g("heritage") and eng_origin and not non_origin):
            return "ambiguous", "conflict"        # grew up with English after all, or a heritage speaker
        if l2 == 1 and eng_origin >= 2 and not (non_origin or non_nation or other or g("immigrant")):
            return "ambiguous", "conflict"        # one "English isn't my first language" from a Texan: mostly a joke
        if not l2 and (eng_origin or eng_res) and not (non_origin or non_nation):
            return "ambiguous", "unspecified"     # "not a native speaker", probably of a language they learn
        return "L2", "explicit"
    if l1:
        return "ambiguous", "conflict"

    # 2. Another native language named, nothing said about English. Any sign of growing up in an English-speaking
    #    country (origin, an immigrant family, or residence without a foreign origin) leaves it open: children of
    #    immigrants name their parents' language as their first, but mostly grow up with English.
    if other:
        family = g("immig_family") and not (non_origin or non_res)     # not a Brazilian in Brazil saying "Latino"
        if eng_origin or out_origin or g("child_en") or family or (eng_res and not non_origin):
            return "ambiguous", "heritage"
        return "L2", "other_l1"

    # 3. Origin.
    foreign = non_origin + non_nation
    if foreign and not (eng_origin or out_origin or g("child_en", "mono", "en_cue")):
        if non_origin or non_nation >= 2 or (non_nation and (non_res or g("nn_cue"))):
            if eng_res and not non_origin:
                return "ambiguous", "heritage"    # "I'm Italian" and lives in New Jersey
            return "L2", "origin"
    if eng_origin and not (foreign or out_origin or g("immigrant", "child_other", "nn_cue", "residence:OUT")):
        # Children of immigrants mostly grow up with English; their cues only weigh against thin evidence.
        # A single nationality statement ("I'm American", "Floridian here") is not enough, however much
        # residence comes with it. Residence alone never gives L1: it includes expats and immigrants.
        # Canadian nationality ("I'm Canadian") does not count here at all, since many Canadians have French
        # or another first language; a Canadian origin ("I'm from Ontario") still does.
        ca = g("nation@ENG:CA")
        eng_nation = g("nation:ENG") - ca
        origin_l1 = eng_origin - ca
        backed = origin_l1 > eng_nation or eng_nation >= 2
        if backed and (origin_l1 >= 2 or not g("heritage")):
            return "L1", "origin"
    return "ambiguous", "none" if not n else "insufficient"


def classify(hits: list[tuple], flairs: list[tuple]) -> tuple[str, str]:
    """(label, basis) of one user from their hits (rule, name, value, context, subreddit) and flairs (subreddit, flair)."""
    user_claims = []
    for rule, name, value, context, subreddit in hits:
        user_claims += [(k, d, name) for k, d in claims.parse(rule, value, context, subreddit)]
    for subreddit, flair in flairs:
        user_claims += [(k, d, f"flair:{subreddit}") for k, d in flair_claims(subreddit, flair)]
    return decide(summarise(user_claims))


def _classify_batch(batch: list[tuple[str, list, list]]) -> list[tuple[str, str, str]]:
    return [(author, *classify(hits, flairs)) for author, hits, flairs in batch]


def _batches(db: sqlite3.Connection, size: int = 2000):
    """All users in author order, each with their hits and flairs, in batches. Both tables are read in index order."""
    hits = groupby(db.execute("select author, rule, name, value, context, subreddit from hits order by author"),
                   key=lambda r: r[0])
    flairs = groupby(db.execute("select author, subreddit, flair from flairs order by author"), key=lambda r: r[0])
    next_hits, next_flairs = next(hits, (None, None)), next(flairs, (None, None))
    batch = []
    for (author,) in db.execute("select author from users order by author"):
        user_hits, user_flairs = [], []
        while next_hits[0] is not None and next_hits[0] < author:    # not in the users table: skip
            next_hits = next(hits, (None, None))
        while next_flairs[0] is not None and next_flairs[0] < author:
            next_flairs = next(flairs, (None, None))
        if next_hits[0] == author:
            user_hits = [r[1:] for r in next_hits[1]]
            next_hits = next(hits, (None, None))
        if next_flairs[0] == author:
            user_flairs = [r[1:] for r in next_flairs[1]]
            next_flairs = next(flairs, (None, None))
        batch.append((author, user_hits, user_flairs))
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def main(out_dir: Path = HERE / "lists") -> None:
    # Pool.imap reads the batches in its own thread; the connection is read-only and only used there.
    db = sqlite3.connect(f"file:{HERE / 'evidence' / 'evidence.sqlite3'}?mode=ro", uri=True, check_same_thread=False)
    total = db.execute("select count(*) from users").fetchone()[0]
    out_dir.mkdir(exist_ok=True)
    counts, done, start = Counter(), 0, time.time()
    lists = {"L1": [], "L2": []}
    with Pool() as pool, open(out_dir / "labels.tsv", "w") as tsv:
        tsv.write("author\tlabel\tbasis\n")
        for rows in pool.imap(_classify_batch, _batches(db)):
            for author, label, basis in rows:
                tsv.write(f"{author}\t{label}\t{basis}\n")
                counts[label, basis] += 1
                if label in lists:
                    lists[label].append(author)
            done += len(rows)
            print(f"\r{done:,}/{total:,} users, {time.time() - start:.0f}s", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)
    for label, names in lists.items():
        (out_dir / f"{label}.txt").write_text("".join(f"{a}\n" for a in names))
    for (label, basis), n in sorted(counts.items()):
        print(f"{label}\t{basis}\t{n:,}\t{n / total:.1%}")
    for label in ("L1", "L2", "ambiguous"):
        n = sum(v for (l, _), v in counts.items() if l == label)
        print(f"{label}: {n:,} ({n / total:.1%})")


if __name__ == "__main__":
    main()

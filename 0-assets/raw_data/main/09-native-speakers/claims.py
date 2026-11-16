"""Reads one regex hit of the scan (scan_histories.py) as typed claims about the user, for classify_users.py.

A claim is (kind, detail). Kinds:
  Language
  - en_l1          English is (one of) the user's native languages ("English is my first language").
  - en_l2          English is not ("I'm not a native English speaker", "sorry for my English", "I'm ESL").
  - other_l1       another language is a native language; detail = the language.
  - nn_unspec      non-native speaker of a language that is not named ("I'm not a native speaker").
  - native_unspec  native speaker of a language that is not named ("as a native speaker").
  - mono           only speaks English ("I'm monolingual", "I only speak English").
  - nn_cue         weaker non-native cues ("in my language", "in German we say", a foreign accent).
  - en_cue         weaker native cues (teaching English abroad, a Southern accent).
  Childhood and family
  - child_en       grew up with English ("I grew up speaking English", "English-speaking household").
  - child_other    grew up with another language; detail = the language.
  - heritage       family from elsewhere, child of immigrants, second generation, parents speak no English.
  - immig_family   (with heritage) the family immigrated to where the user grew up: child of immigrants, second
                   or first generation, Latina, German-American. Mostly said in English-speaking countries.
  - immigrant      the user moved between countries (immigrant, expat, refugee, green card, "my home country").
  Place (detail = "CLASS:country", class from places.py)
  - origin         from, born, grew up, "back home in", "native Texan", "my hometown is".
  - nation         nationality ("I'm German", "Canadian here", "my Irish passport").
  - residence      lives there now ("I live in", "here in").
Statements about others, negated places, hypotheticals and anything the rules cannot read give no claim.
"""
import re

import patterns
import places

LANGS = {w.lower().replace("-", " ") for w in patterns.LANGUAGES.split()} | {"castilian", "farsi", "arab"}
LANG_ALIASES = {"arab": "arabic", "castilian": "spanish", "farsi": "persian", "bangla": "bengali", "mandarin": "chinese",
                "cantonese": "chinese", "flemish": "dutch", "swiss german": "german", "slovene": "slovenian"}
LANG_RX = re.compile(r"\b(" + "|".join(sorted((re.escape(l) for l in LANGS), key=len, reverse=True)) + r")\b")
NEG = re.compile(r"\b(?:not|n't|non|never)\b|n['’]t\b|\bnon\s*-?\s*native")
# Subreddits about learning or using another language: "I'm not a native speaker" there is about that language.
LANGUAGE_SUBS = re.compile(r"(?i)learn|language|lingu|german|deutsch|french|spanish|espanol|japan|nihongo|korean"
                           r"|chinese|mandarin|russian|italian|portug|dutch|svenska|swedish|norsk|norweg|danish|polish"
                           r"|arabic|hebrew|turkish|greek|esperanto|translat|^(?:de|fr|es|it|nl|se|pl)$")
# Before a match: the user is not stating a fact about themselves.
HYPOTHETICAL = re.compile(r"(?:\bif|\bunless|\bwhether|\bpretend(?:ing)?|\bimagine|\bwish|\bwould|\bcould|\bassume[sd]?"
                          r"|\bthink(?:s|ing)?|\bthought|\bguess(?:ed)?|\bsays?|\bsaid|\bclaim(?:s|ed|ing)?|\bbecause\s+you"
                          r"|\blike|\b(?:just\s+)?because|\bas\s+if|\bas\s+though|\bnot\s+that|\bcuz|\bmaybe|\bperhaps|\bmrw|\bmfw|\btfw|\bmrwhen)\s*,?\s*[\"“']?\s*$",
                          re.I)
UNCLOSED_QUOTE = re.compile(r"[\"“][^\"”]*$")
# Fiction, role play and parody: first-person statements there are not about the user.
FICTION_SUBS = re.compile(r"(?i)circlejerk|copypasta|amitheangel|subredditsimulator|writingprompts|nosleep|shortstories"
                          r"|^hfy$|fanfiction|roleplay|^rpg$|twosentencehorror|shortscarystories|^jokes$|totallynotrobots")


def language(text: str) -> str | None:
    m = LANG_RX.search(text.lower())
    return LANG_ALIASES.get(m.group(1), m.group(1)) if m else None


def first_words(text: str, n: int) -> str:
    return " ".join(re.findall(r"[\w'’-]+", text.lower())[:n])


def place_claim(kind: str, value: str, after: str) -> list[tuple[str, str]]:
    if re.match(r"\s*(?:outside|out\s+of)\b", value, re.I):
        return []                          # "I'm from outside the US"
    if re.search(r"\bsizes?\b", value, re.I):
        return []                          # "I'm from US size 9"
    found = places.locate(value, after)
    return [(kind, f"{found[2]}:{found[1]}")] if found else []


def parse(rule: str, value: str, context: str, subreddit: str) -> list[tuple[str, str]]:
    m = re.search(r"⟦(.*?)⟧", context, re.S)
    if not m:
        return []
    match = re.sub(r"\s+", " ", m.group(1))
    low = match.lower().replace("’", "'")
    before = context[max(0, m.start() - 60):m.start()]
    after = context[m.end():m.end() + 80]
    rid = rule.split("_")[0]
    if FICTION_SUBS.search(subreddit) or HYPOTHETICAL.search(before) or UNCLOSED_QUOTE.search(before[-40:]):
        return []
    fn = PARSERS.get(rid)
    value = value if isinstance(value, str) else ""
    return fn(low, value, before.lower(), after, subreddit) if fn else []


# --- A: statements about the native language ---

def a1(low, value, before, after, sub):
    # my native/first language is X
    v = first_words(value, 4)
    lang = language(v)
    if re.match(r"(?:not|n't)\b", v):
        return [("en_l2", "")] if lang == "english" else []
    if re.search(r"\bmy (?:primary|main) language", low):
        return []                          # today's dominant language, not necessarily the first
    if lang == "english":
        return [("en_l1", "")] + [("other_l1", l) for l in [language(v.replace("english", ""))] if l]
    if lang and re.search(r"\bwas\b", low):
        return [("child_other", lang)]     # "my first language was Spanish (but I lost it)"
    return [("other_l1", lang)] if lang else []


A2_RX = re.compile(r"^(?P<x>[\w-]+) (?:is|was|isn't|wasn't|is not|was not)\s+(?:(?:not|actually|technically|only|also|really) )?"
                   r"(?P<det>my|a|the) (?P<kind>[\w-]+)")


def a2(low, value, before, after, sub):
    # X is (not) my first/second language
    m = A2_RX.match(low)
    if not m:
        return []
    self_ = m["det"] == "my" or re.match(r"\s*(?:for|to) me\b", after, re.I)
    if not self_:
        return []
    neg = bool(re.search(r"n't|\bnot\b", low))
    first = m["kind"] in ("native", "first", "mother", "home", "primary", "main", "l1")
    lang = language(m["x"])
    if not neg and m["kind"] in ("primary", "main"):
        return []                          # "English is my primary language now, it wasn't my first"
    if lang == "english":
        if first:
            return [("en_l2", "")] if neg else [("en_l1", "")]
        return [] if neg else [("en_l2", "")]
    if lang and first and not neg:
        return [("child_other", lang)] if re.search(r"\bwas\b", low) else [("other_l1", lang)]
    return []


def native_claim(neg: bool, rest: str, after: str, sub: str) -> list[tuple[str, str]]:
    """Shared by A3-A5: '(non-)native' followed by `rest` (e.g. 'english speaker', 'speaker', 'texan', 'of ohio')."""
    rest = first_words(rest, 4)
    if re.match(r"(?:language|tongue|land|plant|species|american|to)\b", rest):
        return []
    lang = language(first_words(rest, 2)) if not rest.startswith("of ") else language(first_words(rest, 3))
    if lang == "english":
        return [("en_l2", "")] if neg else [("en_l1", "")]
    if lang:
        return [] if neg else [("other_l1", lang)]
    if re.match(r"(?:speaker|level|fluency)\b", rest):
        if LANGUAGE_SUBS.search(sub):
            return []
        return [("nn_unspec", "")] if neg else [("native_unspec", "")]
    if not neg:                            # "a native Texan", "a native of Ohio", "native New Yorker"
        return place_claim("origin", re.sub(r"^of ", "", rest), after)
    return []


OTHERS_VERB = re.compile(r"\s*(?:would|might|could|will|should|does|do|can|may|must|wouldn't|won't|isn't|is|was"
                         r"|knows?|says?|hears?|thinks?)\b", re.I)


def negated_before(before: str) -> bool:
    # "Not native English speaker here", "not a native speaker": the negation stands before the match.
    return bool(re.search(r"\b(?:not|never|non)\s*-?\s*(?:an?\s+)?(?:english\s+|fully\s+)?$", before))


def a3(low, value, before, after, sub):
    neg = bool(re.search(r"\bnot\b|\bnon\s*-?\s*native", low)) or negated_before(before)
    rest = low.split("native", 1)[1]
    if low.startswith(("as ", "being ")):
        # "as a native speaker would say" is about others; "as a native speaker, I ..." is about oneself.
        tail = (rest + " " + after.lower()) if not rest.strip() or "speaker" in rest else after.lower()
        tail = re.sub(r"^\s*(?:[\w-]+\s+)?speaker\b", "", tail) if "speaker" in tail[:30] else tail
        if OTHERS_VERB.match(tail):
            return []
    return native_claim(neg, rest, after, sub)


def a4(low, value, before, after, sub):
    neg = bool(re.search(r"\bnon\s*-?\s*native", low)) or negated_before(before)
    rest = re.sub(r"^(?:non\s*-?\s*)?native\s*", "", low).replace(" here", "")
    return native_claim(neg, rest, after, sub)


def a5(low, value, before, after, sub):
    if not re.search(r"(?:\bi'?m|\bi am|\bas an?)\s+(?:[\w-]+\s+){0,2}$", before):
        return []
    neg = bool(re.search(r"\bnon\s*-?\s*native", low)) or negated_before(before)
    rest = low.split("speaker of", 1)[1]
    lang = language(first_words(rest, 3))
    if lang == "english":
        return [("en_l2", "")] if neg else [("en_l1", "")]
    return [("other_l1", lang)] if lang and not neg else []


def a6(low, value, before, after, sub):
    lang = language(value)
    if not lang or re.search(r"\bfluently\b", low) or re.search(r"\b(?:don't|didn't|never|not)\b", before[-20:]):
        return []
    if low.endswith("as a native") and re.match(r"\s*[\w-]+\s+speaker", after):
        return []                          # "I learned Mandarin as a native English speaker" (A3 reads it)
    if re.search(r"natively|as (?:a|my) (?:first|native|mother)", low):
        return [("en_l1", "")] if lang == "english" else [("other_l1", lang)]
    return [("child_en", "")] if lang == "english" else [("child_other", lang)]   # at home, as a child


def a7(low, value, before, after, sub):
    if re.search(r"lingual", low):
        return [("child_other", "")]       # grew up bilingual: a second language at home
    langs = {language(w) for w in re.split(r",|\band\b|\bor\b|/", first_words(value, 6))} - {None}
    out = [("child_en", "")] if "english" in langs else []
    return out + [("child_other", l) for l in sorted(langs - {"english"})]


def a8(low, value, before, after, sub):
    if re.search(r"mono\s*-?\s*lingual", low) and not re.search(r"\bnot\b", low):
        return [("mono", "")]
    return []


def a9(low, value, before, after, sub):
    if not re.search(r"(?:\bi'?m|\bi am|\bi come|grew up|raised|born|\bi was|\bwe were)\b[^.!?]{0,25}$", before):
        return []
    m = re.search(r"(?:in|from|to) an? ([\w-]+)\s*-\s*speaking (\w+)", low)
    if not m:
        return []
    lang, what = language(m[1]), m[2]
    if not lang:
        return []
    if what in ("household", "family", "home", "parents"):
        return [("child_en", "")] if lang == "english" else [("child_other", lang)]
    if low.startswith("from") or re.search(r"grew up|raised|born", before):
        return [("origin", "ENG:english-speaking")] if lang == "english" else [("origin", f"NON:{lang}-speaking")]
    return []


def a10(low, value, before, after, sub):
    if re.search(r"\bonly speak english\b", low) and not re.search(r"\bnot\b|n't", low):
        return [("mono", "")]
    return []


# --- B: non-native self-disclosure ---

def b1(low, value, before, after, sub):
    return [("en_l2", "")] if low.endswith("english") else []


B2_BAD = re.compile(r"^\s*(?:(?:is|was|isn't|wasn't|has|could|skills|level|grammar|proficiency|vocabulary|are|aren't"
                    r"|is still|still)\s+)*(?:(?:not|n't|really|very|so|pretty|quite|a bit|kinda|kind of|still|too"
                    r"|that|far from|getting|always)\s+)*(?:bad|poor|broken|terrible|horrible|awful|limited|rusty|weak"
                    r"|basic|mediocre|lacking|imperfect|shit|shitty|crap|crappy|sucks|not (?:very |that |so |the )?"
                    r"(?:good|great|perfect|fluent|native|best|the best|my first)|isn't (?:very |that |so )?(?:good|great"
                    r"|perfect|the best)|aren't (?:very |that |so )?(?:good|great)|far from perfect|so-so|meh|not\b)", re.I)


def b2(low, value, before, after, sub):
    if "accent" in low:
        return [("en_cue", "")]
    tail = low.split("english", 1)[1] + " " + after.lower()[:40]
    return [("en_l2", "")] if B2_BAD.match(tail) else []


def b3(low, value, before, after, sub):
    if re.search(r"\b(?:don't|didn't|never|not|want to|wanted to|need to|should)\b", low):
        return []
    tail = (low.split("english", 1)[1] + " " + after.lower()[:60])
    if re.match(r"\s*(?:grammar|lit|literature|major|degree|class|classes|teacher|in college|at university|history)\b", tail):
        return []
    strong = (r"as an? (?:second|foreign|2nd|third|3rd|additional)|from (?:tv|movies|films|video ?games|games|the internet"
              r"|youtube|cartoons|reading|books|songs|music|watching|playing)|by (?:myself|watching|playing|reading|listening"
              r"|ear)|on my own|when i was (?:\d|a|in)|at (?:the )?age (?:of )?\d|at \d|as an adult|as a (?:kid|child|teen"
              r"|teenager)|later in life|since i was|for \d+ years")
    if re.search(r"\blearn", low):           # learned (not studied) it at school: English was not there before
        strong += r"|in (?:primary |elementary |middle |high )?school|at school"
    # "I'm (still) learning English", not "I am so impressed that you are learning English"
    if re.match(r"i(?:'m| am| was)(?: \w+)? learning\b", low) or re.search(strong, tail):
        return [("en_l2", "")]
    return []


def b4(low, value, before, after, sub):
    if re.match(r"i'?\s*m|i am", low):
        if re.match(r"\s*(?:teacher|tutor|instructor|teaching|certified|trained)", after, re.I):
            return [("en_cue", "")]
        return [("en_l2", "")]
    if re.search(r"toefl|ielts|toeic", low) and re.search(
            r"\b(?:i|my)\b[^.!?]{0,30}\b(?:took|take|taking|passed|pass|got|scored|score|did|had|need|have|prepar\w*)\b",
            before[-50:]) and not re.search(r"teach|tutor|student", before[-50:]):
        return [("en_l2", "")]
    return []


def b6(low, value, before, after, sub):
    if "my" in low.split():
        return [("en_l2", "")]
    if re.search(r"\b(?:i|i'm|i am|i've)\b[^.!?]{0,25}\b(?:learn\w*|speak\w*|use|have|studied|stud\w*|took|know)\s*$", before) \
            and not re.search(r"teach", before[-40:]):
        return [("en_l2", "")]
    return []


def b7(low, value, before, after, sub):
    if low.startswith("my "):              # "my mother tongue" alone says nothing about which language
        return []
    lang = language(value) if value else None
    if lang == "english":
        return []
    return [("nn_cue", lang or "")]


def b8(low, value, before, after, sub):
    lang = language(low)
    return [("nn_cue", lang)] if lang and lang != "english" else []


def b9(low, value, before, after, sub):
    return [("nn_cue", "")]


def b10(low, value, before, after, sub):
    v = value.lower()
    if v == "foreign":
        return [("nn_cue", "")]
    found = places.locate(v)
    lang = language(v)
    if (found and found[2] == "ENG") or lang == "english":
        return [("en_cue", "")]
    if (found and found[2] == "NON") or (lang and lang != "english"):
        return [("nn_cue", lang or "")]
    return []


def b11(low, value, before, after, sub):
    return [("heritage", "")] if low.startswith("my ") else []


# --- C: origin and residence ---

def c1(low, value, before, after, sub):
    if re.search(r"\bnot\b", low) or re.match(r"we(?:'re| are) all\b", low):
        return []                          # "We are all from Syria on this blessed day"
    if low.startswith("my "):              # my parents are from ...
        found = places.locate(value, after)
        return [("heritage", "")] if found and found[2] in ("NON", "OUT") else []
    return place_claim("origin", value, after)


def c2(low, value, before, after, sub):
    if low.startswith("born") and not re.search(r"(?:\bi\s*(?:was|'m|am)|\bi've been|[.!?\n]|^)\s*$", before):
        return []                          # "if they had been born and raised in Germany"
    return place_claim("origin", value, after)


def c3(low, value, before, after, sub):
    if re.search(r"\b(?:lived|used to|had)\b", low) and not re.search(r"\b(?:i've|i have)\s+(?:\w+\s+)?lived\b", low):
        return []                          # past residence
    if re.search(r"\b(?:have|'ve)\s+lived\b", low) and not re.search(r"^\s*(?:for|since)\b", after.lower()) \
            and not re.search(r"\b(?:for|since)\b", value.lower()):
        return []                          # "I've lived in Japan" (at some point) vs "... for 10 years"
    return place_claim("residence", value, after)


def c4(low, value, before, after, sub):
    found = places.locate(value, after)
    if not found:
        return []
    cls, country = found[2], found[1]
    if re.search(r"\b(?:moved|emigrated|immigrated|relocated|came over)\s+(?:\w+\s+)?from\b", low):
        return [("origin", f"{cls}:{country}")]
    # Moving to another country (not "I moved to Texas"); a move within a foreign country is rare in English.
    country_level = cls in ("NON", "OUT") or found[0] in places.ENG_COUNTRIES
    out = [("residence", f"{cls}:{country}")]
    if "back" in low.split():
        return out + [("origin", f"{cls}:{country}")]
    if country_level or "came" in low:
        out.append(("immigrant", ""))
    return out


THIRD_PERSON = re.compile(r"\b(?:he|she|they|him|her|them|his|their|someone|somebody|people)\b[^.!?;]{0,60}$")


def c5(low, value, before, after, sub):
    if low.startswith("as ") and (not AS_SELF.match(after) or THIRD_PERSON.search(before)):
        return []                          # "dying to a gun as a foreigner", "she went abroad as an exchange student"
    if re.search(r"second|2nd|generation", low):
        return [("heritage", ""), ("immig_family", "")]
    return [("immigrant", "")]


def c6(low, value, before, after, sub):
    kind = "origin" if low.startswith("back home") else "residence"
    return place_claim(kind, value, after)


def c7(low, value, before, after, sub):
    return place_claim("origin", value, after) if value else []


NOT_NATION = re.compile(r"^\s*(?:speaker|speaking|teacher|tutor|learner|student|major|class|classes|lit|literature"
                        r"|word|words|name|version|translation|accent|dub|subtitles|keyboard|layout|course|test|exam"
                        r"|grammar|spelling|language|lessons?|people|guys?|friends?|fans?)\b", re.I)


# After "as a German" / "being a German": the user is the subject only if the clause goes on like this.
AS_SELF = re.compile(r"\s*(?:$|[,.!?;:)]|(?:myself|i|we|me|who|living|born|citizen|person|guy|man|woman|girl|boy|dude"
                     r"|lady|national|resident|here|and|in|from|with|that|by|of\s+\w+\s+descent)\b)", re.I)


# Before "as a German": the role belongs to someone else or is put on.
OTHER_SUBJECT = re.compile(r"\b(?:himself|herself|themselves|him|her|them|he|she|they|posing|posed|pose|dressed|dress"
                           r"|disguised|pretending|pretend|introduced|treated|seen|regarded|known|portrayed|played)\s*$")


def c8(low, value, before, after, sub):
    if re.search(r"\bnot\b", low) or NOT_NATION.match(after):
        return []
    found = places.locate(value)
    hyphenated = re.match(r"\s*-?\s*(american|canadian|australian|british)\b", after, re.I)
    if hyphenated:                         # German(-)American, Hispanic American: heritage, and the nationality
        country = places.locate(hyphenated[1])
        return ([("heritage", ""), ("immig_family", "")] if found and found[2] != "ENG" else []) + [("nation", f"ENG:{country[1]}")]
    if re.search(r"\bhalf\b", low):
        return [("heritage", "")] if found and found[2] != "ENG" else []   # half German
    if found and found[0] in ("hispanic", "latino", "latina"):
        return [("heritage", ""), ("immig_family", "")]
    if re.search(r"^(?:us|we)\b", low):  # "we Germans", "us Brits": only with a plural demonym
        if not low.endswith("s"):
            return []
    if re.match(r"(?:as|being)\b", low) and (not AS_SELF.match(after) or OTHER_SUBJECT.search(before)):
        return []                          # "as a German tank", "as a Ukrainian said", "declaring himself as a Brit"
    return place_claim("nation", value, after)


def c9(low, value, before, after, sub):
    return place_claim("nation", value, after)


def c10(low, value, before, after, sub):
    if re.search(r"green card|citizenship (?:test|application|ceremony|interview)|naturali|residence permit|work visa"
                 r"|visa application", low):
        return [("immigrant", "")]
    if "dual" in low:                     # "I'm a dual citizen", not "India doesn't allow dual citizenship"
        self_ = re.search(r"(?:\bi'?m|\bi am|\bas|\bmy|\bi have|\bi hold|\bi got|\bi'?ve got)\s+(?:an?\s+|also\s+)?$", before)
        return [("heritage", "")] if self_ else []
    if not (low.startswith("my") or re.search(r"\bi\b[^.!?]{0,15}$", before)):
        return []                          # someone else's passport
    return place_claim("nation", value, after) if value else []


def c11(low, value, before, after, sub):
    found = places.locate(value) if value else None
    if found and found[2] == "ENG":           # "first-generation American": born there to immigrants
        return [("heritage", ""), ("immig_family", ""), ("nation", f"ENG:{found[1]}")]
    if re.search(r"\b(?:first|1st)\s*-?\s*gen", low):
        return [("immigrant", "")]
    if re.match(r"(?:child|son|daughter|kid)\b", low) and not re.search(r"(?:\bi'?m|\bi am|\bas)\s+(?:an?\s+|the\s+)?$", before):
        return []                          # someone else is the child of immigrants
    return [("heritage", ""), ("immig_family", "")]


# --- E: family and childhood ---

def e1(low, value, before, after, sub):
    lang = language(first_words(value, 3))
    return [("heritage", "")] if lang and lang != "english" else []


def e2(low, value, before, after, sub):
    found = places.locate(value)
    return [("heritage", "")] if found and found[2] in ("NON", "OUT") else []


def e3(low, value, before, after, sub):
    langs = {language(w) for w in re.split(r",|\band\b|\bor\b|/", first_words(value, 5))} - {None}
    if not langs:
        return []
    out = [("child_en", "")] if "english" in langs else []
    return out + [("child_other", l) for l in sorted(langs - {"english"})]


def e5(low, value, before, after, sub):
    if "english" in low and re.search(r"taught|learn", low):
        return [("en_l2", "")]
    return []


def e6(low, value, before, after, sub):
    lang = language(first_words(value, 3)) if value else language(low)
    if re.search(r"\b(?:didn't|did not|couldn't|could not|never)\b", low):
        return [("child_other", "")] if lang == "english" else []
    if not lang:
        return []
    return [("child_en", "")] if lang == "english" else [("child_other", lang)]


def d1(low, value, before, after, sub):
    if re.search(r"tefl|tesol|celta", low):
        return [("en_cue", "")] if re.search(r"\b(?:i|my)\b", before[-40:]) else []
    found = places.locate(value) if value else None
    return [("en_cue", "")] if found and found[2] != "ENG" else []


PARSERS = {"A1": a1, "A2": a2, "A3": a3, "A4": a4, "A5": a5, "A6": a6, "A7": a7, "A8": a8, "A9": a9, "A10": a10,
           "B1": b1, "B2": b2, "B3": b3, "B4": b4, "B6": b6, "B7": b7, "B8": b8, "B9": b9, "B10": b10, "B11": b11,
           "C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "C6": c6, "C7": c7, "C8": c8, "C9": c9, "C10": c10,
           "C11": c11, "E1": e1, "E2": e2, "E3": e3, "E5": e5, "E6": e6, "D1": d1}

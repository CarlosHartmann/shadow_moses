"""Regex rules for finding self-disclosures of native language, origin and residence in comments.

Draft of 2026-10-27, tested on samples of the step-4 cross-subreddit data. See notes.txt.
"""
import html
import re
from collections.abc import Iterator

I_AM = r"(?:I\s*['’]?\s*m|I\s+am)"          # I'm, I’m, Im, i m, I am
SPAN = r"(?P<value>[\w'’&-]+(?:[ \t]+[\w'’&-]+){0,5})"   # up to 6 words, never crosses punctuation/newline
LANG = r"(?P<lang>[A-Za-z][\w-]*)"

# Demonyms (countries, plus regions of the main English-speaking countries). Matched case-insensitively.
DEMONYMS = """
American Yank Yankee Brit British English Englishman Englishwoman Scottish Scot Scotsman Scotswoman Welsh Welshman
Irish Irishman Irishwoman Northern-Irish Canadian Canuck Australian Aussie Kiwi New-Zealander South-African
Texan Californian Floridian New-Yorker Midwesterner Southerner Northerner Southern-Californian Hawaiian Alaskan
Londoner Mancunian Scouser Geordie Brummie Yorkshireman Cornish Glaswegian Dubliner Quebecer Québécois Quebecois
Albertan Ontarian Newfie Maritimer Queenslander Tasmanian Victorian Sydneysider Melburnian
German Austrian Swiss Dutch Flemish Belgian French Luxembourger Italian Spanish Spaniard Catalan Basque Galician
Portuguese Brazilian Mexican Argentinian Argentine Chilean Colombian Peruvian Venezuelan Cuban Puerto-Rican
Dominican Ecuadorian Uruguayan Paraguayan Bolivian Guatemalan Honduran Salvadoran Nicaraguan Costa-Rican Panamanian
Jamaican Trinidadian Barbadian Bajan Haitian Bahamian Guyanese Belizean
Swedish Swede Norwegian Danish Dane Finnish Finn Icelandic Icelander Estonian Latvian Lithuanian
Polish Pole Czech Slovak Slovakian Hungarian Romanian Moldovan Bulgarian Serbian Serb Croatian Croat Bosnian
Slovenian Slovene Macedonian Albanian Montenegrin Kosovar Greek Cypriot Maltese Turkish Turk
Russian Ukrainian Belarusian Georgian Armenian Azerbaijani Azeri Kazakh Uzbek
Israeli Palestinian Lebanese Syrian Jordanian Iraqi Iranian Persian Kurdish Saudi Emirati Kuwaiti Qatari Yemeni Omani
Egyptian Moroccan Algerian Tunisian Libyan Nigerian Ghanaian Kenyan Ugandan Tanzanian Ethiopian Eritrean Somali
Sudanese Zimbabwean Zambian Malawian Botswanan Namibian Cameroonian Senegalese Ivorian Congolese Rwandan Afrikaner
Indian Pakistani Bangladeshi Sri-Lankan Nepali Nepalese Afghan Chinese Taiwanese Hongkonger Japanese Korean
Vietnamese Thai Filipino Filipina Pinoy Indonesian Malaysian Singaporean Burmese Cambodian Laotian Mongolian
European Latino Latina Hispanic Asian-American Mexican-American Chinese-American Indian-American Korean-American
"""
DEMONYM = r"(?:" + "|".join(sorted((d.replace("-", r"[\s-]") for d in DEMONYMS.split()), key=len, reverse=True)) + r")"

# Language names, for rules that only make sense with a named language ("in Dutch we say").
LANGUAGES = """
English German Dutch Flemish French Italian Spanish Catalan Basque Galician Portuguese Romanian Swedish Norwegian
Danish Finnish Icelandic Estonian Latvian Lithuanian Polish Czech Slovak Hungarian Bulgarian Serbian Croatian
Bosnian Slovenian Slovene Macedonian Albanian Greek Maltese Turkish Russian Ukrainian Belarusian Georgian Armenian
Azerbaijani Kazakh Uzbek Hebrew Arabic Persian Farsi Kurdish Urdu Hindi Bengali Bangla Punjabi Gujarati Marathi
Tamil Telugu Kannada Malayalam Sinhala Nepali Chinese Mandarin Cantonese Taiwanese Japanese Korean Vietnamese Thai
Tagalog Filipino Indonesian Malay Burmese Khmer Mongolian Swahili Amharic Somali Yoruba Igbo Hausa Afrikaans Zulu
Xhosa Welsh Irish Gaelic Scots Frisian Luxembourgish Swiss-German Esperanto Latin Yiddish Creole Patois
"""
LANGUAGE = r"(?:" + "|".join(sorted((w.replace("-", r"[\s-]") for w in LANGUAGES.split()), key=len, reverse=True)) + r")"

RELATIVES = (r"(?:parents|family|mom|mum|mother|dad|father|grandparents|grandma|grandmother|granny|grandpa|grandfather"
             r"|nan|nana|relatives|siblings|brothers?|sisters?|aunts?|uncles?|cousins|folks)")
SCHOOL = r"(?:school|kindergarten|preschool|daycare|high\s*school|college|university|program|programme|classes|class)"
# The next word is a language name or capitalised (but not "I"): keeps "my dad talked trash" and "I knew better" out.
LANG_AHEAD = r"(?=(?:" + LANGUAGE + r")\b|(?-i:[A-Z])(?!(?-i:I\b)))"

# (rule id, category, trigger substrings (lowercase; rule only runs if one is present), pattern)
RULES = [
    # --- A: explicit statements about L1 / native-speaker status ---
    ("A1_my_l1_is", "L1", ["language", "tongue", "l1"],
     r"\bmy\s+(?:(?:native|first|mother|home|primary|main|birth|original)\s+(?:language|tongue)|mother\s*-?\s*tongue|L1)"
     r"\s+(?:is|was|being|=)\s+(?:(?:actually|technically|obviously|probably|still)\s+)?" + SPAN),
    ("A2_x_is_my_lx", "L1", ["language", "tongue", "l1", "l2"],
     r"\b" + LANG + r"\s+(?:is|was|isn['’]t|wasn['’]t|is\s+not|was\s+not)\s+(?:(?:not|actually|technically|only|also|really)\s+)?"
     r"(?:my|a|the)\s+(?:(?:native|first|mother|home|primary|main|second|third|fourth|foreign|2nd|3rd|4th)\s+(?:language|tongue)|mother\s*-?\s*tongue|L1|L2)"),
    ("A3_i_am_native", "L1", ["native"],
     r"\b(?:" + I_AM + r"|as\s+(?:a|an|another|someone|somebody)|being\s+(?:a|an))\s+(?:(?:not|also|a|an|actually|myself|too|technically|basically|fellow)\s+){0,3}"
     r"(?P<neg>non\s*-?\s*)?native\b(?!\s+(?:Americans?|to)\b)(?:\s+" + SPAN + r")?"),
    ("A4_native_x_speaker_here", "L1", ["native"],
     r"\b(?:non\s*-?\s*)?native(?:\s+" + LANG + r")?\s+speaker\s+here\b"),
    ("A5_native_speaker_of", "L1", ["native"],
     r"\b(?:non\s*-?\s*)?native\s+speaker\s+of\s+" + SPAN),
    ("A6_speak_natively", "L1", ["native", "fluent", "at home", "as a child", "as a kid"],
     r"\bI\s+(?:speak|spoke|learned|learnt|know)\s+" + LANG + r"\s+(?:natively|fluently|at\s+home|as\s+(?:a|my)\s+(?:child|kid|first|native|mother))"),
    ("A7_grew_up_speaking", "L1", ["speaking", "lingual"],
     r"\b(?:I\s+(?:grew\s+up|was\s+raised|was\s+brought\s+up)|we\s+were\s+raised)\s+"
     r"(?:speaking\s+" + SPAN + r"|(?:bi|tri|multi)\s*-?\s*lingual)"),
    ("A8_i_am_bilingual", "L1", ["lingual"],
     r"\b" + I_AM + r"\s+(?:(?:a|also|actually|fully|natively|basically|completely|only|sadly|just)\s+){0,2}(?:mono|bi|tri|multi)\s*-?\s*lingual\b(?:\s+" + SPAN + r")?"),
    ("A9_x_speaking_home", "L1", ["speaking"],
     r"\b(?:in|from|to)\s+(?:a|an)\s+(?P<lang>[\w-]+)\s*-\s*speaking\s+(?:household|family|home|country|parents|region|province|part|area|town|village)\b"),
    ("A10_i_speak_lang", "L1", ["speak", "spoke"],
     r"\bI\s+(?:(?:also|can|could|only|still|do|don['’]t|mostly|natively)\s+)*(?:speak|spoke)\s+"
     r"(?:(?:fluent|some|a\s+(?:bit|little)\s+(?:of\s+)?|basic|conversational|decent|good|bad|broken|native|perfect|only)\s+)?"
     r"(?P<value>(?-i:[A-Z][a-z]+)(?:\s*(?:,|and|&)\s*(?-i:[A-Z][a-z]+))*)"),
    # --- B: non-native self-disclosure ---
    ("B1_sorry_my_english", "nonnative", ["english", "grammar", "spelling"],
     r"\b(?:sorry|excuse|forgive|pardon|apologi[sz]e|apologies)\s+(?:me\s+)?(?:for\s+|about\s+)?(?:my|the)\s+"
     r"(?:(?:bad|poor|broken|terrible|horrible|crappy|shitty|limited|imperfect|awful|rusty|weird|english)\s+)?(?:English|grammar|spelling)\b"),
    ("B2_my_english", "nonnative", ["english"],
     r"\bmy\s+(?:(?:bad|poor|broken|limited|written|spoken)\s+)?English\s+(?:is|isn['’]t|was|has|could|sucks|skills|level|grammar|proficiency|vocabulary|accent)\b"),
    ("B3_i_learned_english", "nonnative", ["english"],
     r"\bI\b[^.!?\n]{0,30}?\b(?:learn(?:ed|t|ing)?|stud(?:ied|ying|y)|taught\s+myself|picked\s+up|acquired)\s+English\b(?!\s+(?:lit|literature|major|degree))(?:\s+" + SPAN + r")?"),
    ("B4_i_am_esl", "nonnative", ["esl", "efl", "toefl", "ielts", "toeic", "cae", "cpe", "cambridge"],
     r"\b" + I_AM + r"\s+(?:(?:an|a|also|actually)\s+)?(?-i:ESL|EFL)\b|(?-i:\b(?:TOEFL|IELTS|TOEIC|CAE|CPE)\b)|\bCambridge\s+(?:English|Advanced|Proficiency|First|Certificate)\b"),
    ("B5_non_native", "nonnative", ["native"],
     r"\bnon\s*-?\s*native\b"),
    ("B6_english_second_lang", "nonnative", ["english"],
     r"\bEnglish\s+as\s+(?:a|my)\s+(?:second|third|foreign|2nd|3rd)\s+language\b"),
    ("B7_in_my_language", "nonnative", ["language", "tongue"],
     r"\b(?:in|from|into)\s+my\s+(?:own\s+|native\s+|mother\s+|first\s+|home\s+)?language\b(?!\s+(?:use|choice|skills))(?:\s*\(\s*(?P<value>[^)\n]{1,30})\))?"
     r"|\bmy\s+(?:mother\s*-?\s*tongue|native\s+tongue)\b"),
    ("B8_in_x_we_say", "nonnative", ["we ", "word", "saying", "expression"],
     r"\bin\s+(?P<lang>" + LANGUAGE + r")\s*,?\s+we\s+(?:(?:also|usually|often|just|actually)\s+)?(?:say|call|have|use|write|pronounce|spell)\b"
     r"|\bwe\s+(?:(?:also|actually)\s+)?(?:say|call\s+(?:it|this|that|them)|have\s+(?:a|the\s+same)\s+(?:word|saying|expression|phrase))\b[^.!?\n]{0,40}?\bin\s+(?P<value>" + LANGUAGE + r")\b"),
    ("B9_english_word_trouble", "nonnative", ["english", "translat"],
     r"\b(?:don['’]t|do\s+not|didn['’]t|not\s+sure|can['’]t\s+(?:remember|recall|find|think\s+of)|forgot|forget)\b[^.!?\n]{0,25}?\bEnglish\s+(?:word|term|name|expression|equivalent|translation)\b"
     r"|\b(?:how\s+(?:do\s+you|to|would\s+you|do\s+I)\s+say|what['’]?s\s+(?:it|this|that)\s+called|what\s+is\s+(?:it|this|that)\s+called|what\s+do\s+you\s+call)\b[^.!?\n]{0,40}?\bin\s+English\b"
     r"|\b(?:is|was)\s+(?:this|that|it|my\s+\w+)\s+(?:even\s+)?(?:correct|proper|right|good|bad|wrong|grammatically\s+correct)\s+English\b"
     r"|\btranslat(?:ed|ing|e)\s+(?:it\s+|this\s+|that\s+)?(?:directly\s+|literally\s+)?(?:from|out\s+of)\s+(?P<lang>" + LANGUAGE + r")\b"),
    ("B10_my_accent", "nonnative", ["accent"],
     r"\bmy\s+(?:(?:thick|heavy|strong|slight|noticeable|obvious|foreign|non-native|weird)\s+)*(?P<value>foreign|" + LANGUAGE + r"|" + DEMONYM + r")\s+accent\b"
     r"|\b(?:I\s+have|I\s*['’]?\s*ve\s+got|I\s+still\s+have)\s+(?:a|an)\s+(?:(?:thick|heavy|strong|slight|noticeable|obvious)\s+)*(?P<value2>foreign|" + LANGUAGE + r"|" + DEMONYM + r")\s+accent\b"),
    ("B11_family_no_english", "nonnative", ["english"],
     r"\b(?:my\s+(?:parents|mom|mum|mother|dad|father|family|grandparents|grandma|grandmother|grandpa|grandfather)|we|I)\s+(?:(?:still|barely|never|only|really)\s+)?"
     r"(?:don['’]t|doesn['’]t|didn['’]t|do\s+not|does\s+not|did\s+not|couldn['’]t|can['’]t|could\s+not|never)\s+(?:speak|spoke|know|knew|talk)\s+(?:(?:much|any|good|fluent|a\s+word\s+of)\s+)?English\b"
     r"(?:\s+(?:at\s+home|until|before|when)\b)?"),
    # --- C: origin / residence ---
    ("C1_i_am_from", "origin", ["from"],
     r"\b(?:" + I_AM + r"|I\s+come|we\s*(?:are|['’]re)|my\s+" + RELATIVES + r"\s+(?:is|are|was|were|comes?|came))\s+"
     r"(?:(?:originally|also|actually|not|initially|both|all)\s+)*from\s+(?:the\s+)?" + SPAN),
    ("C2_grew_up_in", "origin", ["grew", "born", "raised", "brought up", "childhood", "spent my"],
     r"\b(?:I\s+(?:grew\s+up|was\s+(?:born|raised|brought\s+up)|spent\s+my\s+(?:childhood|youth|teens|early\s+years))|born\s+and\s+raised|(?:I\s+was|I\s*['’]?\s*m|we\s+were)\s+(?:born\s+and\s+)?raised)"
     r"\s+(?:and\s+(?:raised|bred)\s+)?(?:in|on|near|around|outside(?:\s+of)?)\s+(?:the\s+)?(?!(?:a|an)\s)" + SPAN),
    ("C3_i_live_in", "origin", ["live", "resid"],
     r"\bI\s*(?:['’]?\s*ve|\s+have|\s+had|\s+currently|\s+now|\s+still|\s+actually|\s+used\s+to|\s+also)?\s*(?:live|lived|am\s+living|['’]m\s+living|reside|resided|been\s+living)\s+"
     r"(?:(?:here\s+)?(?:in|on|near|outside(?:\s+of)?))\s+(?:the\s+)?" + SPAN),
    ("C4_moved_to", "origin", ["moved", "migrated", "relocated", "came over", "came to"],
     r"\bI\s+(?:moved|emigrated|immigrated|relocated|came\s+over)\s+(?:here\s+|back\s+|away\s+|over\s+)?(?:to|from)\s+(?:the\s+)?(?!being\b)" + SPAN
     + r"|\bwhen\s+I\s+(?:first\s+)?came\s+(?:over\s+)?to\s+(?:the\s+)?(?=(?-i:[A-Z]))" + SPAN.replace("value", "value2")),
    ("C5_immigrant", "origin", ["migra", "expat", "ex-pat", "refugee", "foreigner", "student"],
     r"\b(?:" + I_AM + r"|as\s+(?:a|an))\s+(?:(?:a|an|also|myself|first|second|1st|2nd|generation|legal|recent)\s+|[\w-]+\s+)?(?:immigrant|expat|ex-pat|emigrant|migrant|refugee|foreigner|international\s+student|exchange\s+student)\b"),
    ("C6_here_in", "origin", ["here in", "home in"],
     r"\b(?:here|over\s+here|back\s+home|down\s+here|up\s+here|out\s+here)\s+in\s+(?:the\s+)?(?=(?-i:[A-Z]))" + SPAN),
    ("C7_my_country", "origin", ["my ", "hometown", "home town"],
     r"\bmy\s+(?:home\s+|native\s+|own\s+|birth\s+)?(?:country|homeland|motherland|fatherland)\b(?:\s*(?:\(|,|is|of)\s*" + SPAN + r")?"
     r"|\bmy\s+home\s*town\s+(?:is|of|was|,|\()\s*" + SPAN.replace("value", "value2")),
    ("C8_i_am_nationality", "origin", [],
     r"\b(?:" + I_AM + r"|as\s+(?:a|an)|we|us|being\s+(?:a|an)?)\s+(?:(?:a|an|also|not|half|proud|born|fellow|actually|myself|fellow|full)\s+){0,2}(?P<value>" + DEMONYM + r")s?\b"),
    ("C9_nationality_here", "origin", [" here"],
     r"(?:^|[.!?]\s+|\n)\s*(?:(?:fellow|another|a|an|as\s+an?|proud)\s+)?(?P<value>" + DEMONYM + r")\s+here\b"),
    ("C10_citizenship", "origin", ["passport", "citizen", "green card", "visa", "naturali", "permit"],
     r"\bmy\s+(?:green\s+card|citizenship\s+(?:test|application|ceremony|interview)|naturali[sz]ation|residence\s+permit|work\s+visa|visa\s+application)\b"
     r"|\b(?:my|a|an|have|hold)\s+(?:(?:dual|second)\s+)?(?P<value>" + DEMONYM + r")\s+(?:passport|citizenship)\b"
     r"|\bdual\s+(?:citizen|citizenship|national|nationality)\b"),
    ("C11_heritage", "origin", ["immigra", "emigra", "moved", "came", "fled", "generation", "gen "],
     r"\bmy\s+(?:parents|family|mom|mum|mother|dad|father|grandparents)\s+(?:(?:both|all|originally)\s+)?(?:immigrated|emigrated|moved|fled|came\s+(?:here|over))\s+(?:here\s+|over\s+)?(?:to|from)\s+(?:the\s+)?" + SPAN
     + r"|\b(?:child|son|daughter|kid)\s+of\s+(?:(?:\w+|first[\s-]generation)\s+)?(?:immigrants|refugees)\b"
     + r"|\b(?:" + I_AM + r"|as)\s+(?:a\s+)?(?:first|second|third|1st|2nd|3rd)\s*-?\s*gen(?:eration)?\b(?:\s+" + SPAN.replace("value", "value2") + r")?"),
    # --- E: family and childhood language environment ---
    ("E1_family_speaks", "childhood", ["speak", "spoke", "talk"],
     r"\bmy\s+(?:(?:own|whole|immediate|extended|mother['’]s|father['’]s|mom['’]s|mum['’]s|dad['’]s)\s+)?" + RELATIVES
     + r"\s+(?:(?:both|all|only|mostly|still|always|also|just|usually|would|used\s+to)\s+)*(?:speak|spoke|speaks|talk|talked|talks)\s+"
     r"(?:(?:to|with)\s+(?:me|us|each\s+other)\s+)?(?:in\s+)?(?:(?:fluent|only|mostly|broken|some|a\s+mix\s+of)\s+)?" + LANG_AHEAD + SPAN),
    ("E2_family_origin", "childhood", ["mom", "mum", "mother", "dad", "father", "parent", "grand", "famil", "folks"],
     r"\bmy\s+(?:(?:mother['’]s|father['’]s|mom['’]s|mum['’]s|dad['’]s)\s+side|" + RELATIVES + r")\s+(?:is|are|was|were)\s+"
     r"(?:(?:both|all|originally|also|half|full|first[\s-]generation|immigrants?|a|an)\s+)*(?:(?P<value>" + DEMONYM + r")\b|native\s+" + SPAN.replace("value", "value2") + r")"),
    ("E3_home_language", "childhood", ["home", "household"],
     r"\b(?:at\s+home|in\s+(?:our|my|the)\s+(?:home|house|household|family))\s*,?\s+(?:we|I)\s+(?:(?:only|always|mostly|usually|still|just|all)\s+)?(?:speak|spoke|talk|talked|used)\s+(?:in\s+)?" + LANG_AHEAD + SPAN
     + r"|\b(?:we|I)\s+(?:(?:only|always|mostly|usually|still|just|all)\s+)?(?:speak|spoke|talk|talked)\s+(?:in\s+)?" + LANG_AHEAD + r"(?P<value2>[\w-]+(?:\s+(?:and|or)\s+[\w-]+)?)\s+at\s+home\b"
     + r"|\b(?:home|household|family)\s+language\b|\blanguage\s+(?:we\s+)?(?:spoke|spoken|speak|used)\s+at\s+home\b"),
    ("E4_school_type", "childhood", ["school", "kindergarten", "daycare", "program", "class", "college", "university"],
     r"\bI\s+(?:went\s+to|attended|was\s+(?:in|at|sent\s+to|enrolled\s+in)|grew\s+up\s+(?:going\s+to|in))\s+(?:an?|the)\s+"
     r"(?P<value>(?:[\w-]+\s+){0,3}?(?:[\w-]+\s*-\s*(?:speaking|medium|language)|international|bilingual|immersion|" + LANGUAGE + r"|" + DEMONYM + r")(?:\s+[\w-]+){0,2}?)\s+"
     + SCHOOL + r"\b"),
    ("E5_school_language", "childhood", ["school", "class", "lesson", "taught"],
     r"\b(?:at|in)\s+(?:my\s+|the\s+|primary\s+|elementary\s+|high\s+|middle\s+|grade\s+|secondary\s+)?school\s*,?\s+(?:I|we)\s+(?:(?:all|only|also|first|always|still|even)\s+)?"
     r"(?:had\s+to|were\s+(?:taught|made|forced|required|supposed|not\s+allowed)\s+to|was\s+(?:taught|made|forced|required|not\s+allowed)\s+to|weren['’]t\s+allowed\s+to|wasn['’]t\s+allowed\s+to"
     r"|learned|learnt|spoke|speak|studied|were\s+taught|was\s+taught|got\s+taught)\s+(?:(?:to\s+)?(?:speak|use|write|read)\s+)?(?:in\s+)?" + LANG_AHEAD + SPAN
     + r"|\bI\s+(?:was|got)\s+taught\s+(?:to\s+(?:speak|read|write)\s+)?(?P<lang>[\w-]+)\s+(?:at|in)\s+(?:primary\s+|elementary\s+|high\s+|middle\s+)?school\b"
     + r"|\b(?P<value2>[A-Za-z][\w-]*)\s+(?:was|is)\s+(?:the\s+)?(?:(?:main|only|primary|official|first|teaching|sole)\s+)?language\s+(?:at|in|of)\s+(?:my\s+|our\s+|the\s+)?school\b"
     + r"|\b(?:classes|lessons|school|everything|teaching)\s+(?:were|was)\s+(?:all\s+|only\s+|mostly\s+|entirely\s+)?(?:taught\s+|held\s+|done\s+)?in\s+(?P<value3>" + LANGUAGE + r")\b"),
    ("E6_childhood_language", "childhood", ["kid", "child", "toddler", "baby", "when i was", "first words", "first language", "until", "till", "before"],
     r"\b(?:as\s+a\s+(?:kid|child|toddler|baby|little\s+\w+)|when\s+I\s+was\s+(?:a\s+)?(?:kid|child|little|young|small|younger|toddler|baby|\d{1,2}))\s*,?\s+"
     r"I\s+(?:(?:only|mostly|also|still|just|already|first)\s+)?(?:spoke|speak|talked|knew|understood|learned|learnt)\s+(?:in\s+)?" + LANG_AHEAD + SPAN
     + r"|\bmy\s+first\s+words\s+were\s+(?:in\s+)?" + SPAN.replace("value", "value2")
     + r"|\bthe\s+first\s+language\s+I\s+(?:ever\s+)?(?:learned|learnt|spoke|knew|was\s+taught)\b(?:\s+(?:was|is)\s+" + SPAN.replace("value", "value4") + r")?"
     + r"|\bI\s+(?:didn['’]t|did\s+not|couldn['’]t|could\s+not|never)\s+(?:learn|learned|speak|spoke|know|understand)\s+(?:(?:any|much|a\s+word\s+of)\s+)?(?P<lang>" + LANGUAGE + r")\s+(?:until|till|before)\s+" + SPAN.replace("value", "value3")),
    # --- D: native-English-leaning experiences ---
    ("D1_taught_english_abroad", "native_hint", ["english", "esl", "efl", "tefl", "celta"],
     # Active voice only: "I was taught English" is about learning it (E5).
     r"\b(?:I\s+(?:(?:have|had|used\s+to|also|once|currently|now|still|will|would|want\s+to|plan\s+to|['’]ve|['’]d|['’]ll)\s+)*(?:taught|teach)"
     r"|I\s*(?:['’]m|\s+am|\s+was|\s+have\s+been|['’]ve\s+been)\s+(?:(?:also|currently|now|still)\s+)?teaching)"
     r"\s+(?:(?-i:ESL|EFL)|English)\b(?:\s+(?:in|at)\s+" + SPAN + r")?|(?-i:\b(?:TEFL|TESOL|CELTA)\b)"),
]
COMPILED = [(rid, cat, trig, re.compile(p, re.IGNORECASE)) for rid, cat, trig, p in RULES]

QUOTE_LINE = re.compile(r"^\s*>.*$", re.MULTILINE)


def clean(body: str) -> str:
    # Bodies are HTML-escaped (&gt;); quoted lines are someone else's words.
    return QUOTE_LINE.sub("", html.unescape(body))


def find(text: str) -> Iterator[tuple[str, str, re.Match, str]]:
    # Yields (rule id, category, match, captured value). A rule only runs if one of its
    # trigger substrings occurs in the comment, which makes matching about six times faster.
    low = text.lower()
    for rid, cat, trig, rx in COMPILED:
        if trig and not any(t in low for t in trig):
            continue
        for m in rx.finditer(text):
            g = {k: v for k, v in m.groupdict().items() if v}
            yield rid, cat, m, g.get("value") or g.get("value2") or g.get("value3") or g.get("value4") or g.get("lang") or ""


"""Place and demonym gazetteer for the L1/L2 classifier (classify_users.py).

Every name maps to a country (or region) and a class:
- ENG: overwhelming English-speaking majority (US, UK, Ireland, Australia, New Zealand, Canada outside Quebec).
- NON: English is not the majority native language there (also Quebec and Puerto Rico).
- OUT: outer-circle countries, where English is official or widespread but rarely the only native language
  (India, Nigeria, the Philippines, South Africa, Singapore, the Caribbean, ...). Origin there confirms neither
  label (notes.txt, Definitions).
- AMB: too vague or ambiguous to place (Europe, the South, Georgia, Montreal, Latino, ...).
"""
import re

ENG = {
    "US": """us|usa|u.s.|u.s.a.|united states|united states of america|america|the states|states|murica|merica
        stateside|american|yank|yankee|texan|californian|floridian|new-yorker|midwesterner|southern-californian
        hawaiian|alaskan|asian-american|mexican-american|chinese-american|indian-american|korean-american
        alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|hawaii|idaho|illinois|indiana
        iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana
        nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon
        pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington
        west virginia|wisconsin|wyoming|ny|nyc|nj|la|sf|dc|d.c.|socal|norcal|so cal|nor cal|pnw|pacific northwest
        midwest|mid west|new england|east coast|west coast|bay area|the bay|upstate new york|upstate ny|long island
        deep south|tri-state area|dfw|bible belt|rust belt|new york city|los angeles|chicago|houston|phoenix
        philadelphia|philly|san antonio|san diego|dallas|austin|san jose|jacksonville|fort worth|columbus|charlotte
        indianapolis|seattle|denver|boston|el paso|nashville|detroit|oklahoma city|portland|las vegas|vegas|memphis
        louisville|baltimore|milwaukee|albuquerque|tucson|fresno|sacramento|kansas city|atlanta|omaha|raleigh|miami
        minneapolis|tulsa|cleveland|wichita|new orleans|tampa|honolulu|pittsburgh|cincinnati|st louis|st. louis
        saint louis|orlando|salt lake city|boise|spokane|des moines|anchorage|brooklyn|queens|the bronx|bronx
        manhattan|staten island|san francisco|oakland|orange county|long beach|chicagoland|twin cities|ann arbor
        green bay|cali|cincy|nola|atl|dmv|chi-town|chitown|so-cal|nyc area|the midwest|the south east us
        tx|fl|az|nc|sc|ma|wa|va|md|mn|mi|wi|il|ga|ct|ut|nv|ky|tn|ks|ia|nm|nh|ri|vt|wv|pa""",   # unambiguous state codes
    "GB": """uk|u.k.|united kingdom|britain|great britain|england|scotland|wales|northern ireland|brit|british
        english|englishman|englishwoman|scottish|scot|scotsman|scotswoman|welsh|welshman|northern-irish|londoner
        mancunian|scouser|geordie|brummie|yorkshireman|cornish|glaswegian|london|manchester|birmingham|liverpool|leeds
        sheffield|bristol|newcastle|nottingham|leicester|coventry|bradford|southampton|portsmouth|brighton|plymouth
        oxford|norwich|wolverhampton|sunderland|essex|kent|surrey|sussex|yorkshire|lancashire|cornwall|devon
        somerset|norfolk|suffolk|cumbria|midlands|the midlands|east anglia|home counties|glasgow|edinburgh|aberdeen
        dundee|inverness|highlands|cardiff|swansea|belfast|derry|londonderry""",
    "IE": """ireland|republic of ireland|eire|irish|irishman|irishwoman|dubliner|dublin|cork|galway|limerick
        waterford|kilkenny|donegal|wicklow""",
    "AU": """australia|aus|oz|straya|australian|aussie|queenslander|tasmanian|sydneysider|melburnian|sydney|melbourne
        brisbane|perth|adelaide|canberra|hobart|gold coast|queensland|new south wales|nsw|tasmania|western australia
        south australia|northern territory|outback""",
    "NZ": "new zealand|nz|aotearoa|kiwi|new-zealander|auckland|wellington|christchurch|dunedin",
    "CA": """canada|canadian|canuck|albertan|ontarian|newfie|maritimer|ontario|british columbia|bc|alberta|manitoba
        saskatchewan|nova scotia|new brunswick|newfoundland|labrador|prince edward island|pei|yukon
        northwest territories|nunavut|maritimes|the maritimes|atlantic canada|western canada|toronto|vancouver
        calgary|edmonton|ottawa|winnipeg|halifax|kitchener|saskatoon|gta|lower mainland|vancouver island|kelowna""",
}

NON = {
    # Countries: the name and its demonyms. Everything that is not listed under ENG, OUT or AMB.
    "": """afghanistan|afghan|albania|albanian|algeria|algerian|andorra|angola|argentina|argentinian|argentine
        armenia|armenian|austria|austrian|azerbaijan|azerbaijani|azeri|bahrain|belarus|belarusian|belgium|belgian
        benin|bhutan|bolivia|bolivian|bosnia|bosnia and herzegovina|bosnian|brazil|brasil|brazilian|brunei|bulgaria
        bulgarian|burkina faso|burundi|cambodia|cambodian|cameroon|cameroonian|cape verde|chad|chile|chilean|china
        chinese|prc|colombia|colombian|comoros|congo|congolese|drc|costa rica|costa-rican|croatia|croatian|croat|cuba
        cuban|cyprus|cypriot|czech republic|czechia|czech|denmark|danish|dane|djibouti|dominican republic|dominican
        ecuador|ecuadorian|egypt|egyptian|el salvador|salvadoran|eritrea|eritrean|estonia|estonian|ethiopia|ethiopian
        finland|finnish|finn|france|french|gabon|germany|deutschland|german|greece|greek|guatemala|guatemalan|guinea
        haiti|haitian|honduras|honduran|hungary|hungarian|iceland|icelandic|icelander|indonesia|indonesian|iran
        iranian|persian|iraq|iraqi|kurdish|israel|israeli|italy|italian|ivory coast|ivorian|japan|japanese|jordan
        jordanian|kazakhstan|kazakh|kosovo|kosovar|kuwait|kuwaiti|kyrgyzstan|laos|laotian|latvia|latvian|lebanon
        lebanese|libya|libyan|liechtenstein|lithuania|lithuanian|luxembourg|luxembourger|madagascar|mali|mauritania
        mexico|mexican|moldova|moldovan|monaco|mongolia|mongolian|montenegro|montenegrin|morocco|moroccan|mozambique
        myanmar|burma|burmese|nepal|nepali|nepalese|netherlands|the netherlands|holland|dutch|flemish|nicaragua
        nicaraguan|niger|north korea|north macedonia|macedonia|macedonian|norway|norwegian|oman|omani|palestine
        palestinian|panama|panamanian|paraguay|paraguayan|peru|peruvian|poland|polish|pole|portugal|portuguese|qatar
        qatari|romania|romanian|russia|russian federation|russian|san marino|saudi arabia|saudi|senegal|senegalese
        serbia|serbian|serb|slovakia|slovak|slovakian|slovenia|slovenian|slovene|somalia|somali|south korea|korea
        korean|spain|spanish|spaniard|catalan|basque|galician|sudan|sudanese|south sudan|suriname|sweden|swedish
        swede|switzerland|swiss|syria|syrian|taiwan|taiwanese|tajikistan|thailand|thai|togo|tunisia|tunisian|turkey
        turkish|turk|turkmenistan|ukraine|ukrainian|united arab emirates|uae|emirati|uruguay|uruguayan|uzbekistan
        uzbek|venezuela|venezuelan|vietnam|viet nam|vietnamese|yemen|yemeni|faroe islands|greenland|puerto rico
        puerto-rican|quebec|québec|quebecer|québécois|quebecois|french guiana|martinique|guadeloupe|new caledonia
        tahiti|french polynesia|macau|tibet|afrikaner""",
    # Regions and cities (mapped to their country only loosely: the class is what counts).
    "region": """catalonia|catalunya|basque country|andalusia|bavaria|bayern|flanders|wallonia|brittany|normandy
        provence|alsace|sicily|sardinia|tuscany|lombardy|the nordics|nordics|latin america|south america
        central america|middle east|the middle east|east asia|southeast asia|berlin|munich|hamburg|cologne|frankfurt
        stuttgart|dusseldorf|düsseldorf|leipzig|dresden|hanover|nuremberg|bremen|vienna|wien|zurich|zürich|geneva
        basel|bern|paris|lyon|marseille|toulouse|bordeaux|lille|strasbourg|nantes|brussels|antwerp|ghent|amsterdam
        rotterdam|the hague|utrecht|eindhoven|groningen|copenhagen|aarhus|stockholm|gothenburg|malmo|malmö|oslo
        bergen|trondheim|helsinki|tampere|reykjavik|tallinn|riga|vilnius|warsaw|krakow|kraków|wroclaw|gdansk|poznan
        prague|brno|bratislava|budapest|ljubljana|zagreb|belgrade|sarajevo|sofia|bucharest|cluj|chisinau|athens
        thessaloniki|istanbul|ankara|izmir|moscow|st petersburg|saint petersburg|kyiv|kiev|kharkiv|odessa|odesa|lviv
        minsk|tbilisi|yerevan|baku|madrid|barcelona|valencia|seville|sevilla|bilbao|malaga|lisbon|lisboa|porto|rome
        milan|milano|naples|turin|florence|venice|bologna|genoa|palermo|tokyo|osaka|kyoto|yokohama|seoul|busan
        beijing|shanghai|shenzhen|guangzhou|chengdu|wuhan|taipei|bangkok|jakarta|hanoi|ho chi minh city|saigon
        mexico city|guadalajara|monterrey|tijuana|cancun|sao paulo|são paulo|rio de janeiro|brasilia|buenos aires
        bogota|bogotá|medellin|medellín|lima|caracas|quito|montevideo|havana|tel aviv|jerusalem|haifa|dubai
        abu dhabi|riyadh|tehran|baghdad|beirut|cairo|casablanca|tunis|algiers|quebec city|gatineau""",
}

OUT = """india|indian|pakistan|pakistani|bangladesh|bangladeshi|sri lanka|sri-lankan|nigeria|nigerian|ghana|ghanaian
    kenya|kenyan|uganda|ugandan|tanzania|tanzanian|zambia|zambian|zimbabwe|zimbabwean|malawi|malawian|botswana
    botswanan|namibia|namibian|south africa|south-african|rsa|sierra leone|liberia|gambia|the gambia|rwanda|rwandan
    eswatini|swaziland|lesotho|philippines|the philippines|filipino|filipina|pinoy|singapore|singaporean|malaysia
    malaysian|hong kong|hongkonger|jamaica|jamaican|trinidad|trinidad and tobago|trinidadian|barbados|barbadian|bajan
    bahamas|the bahamas|bahamian|guyana|guyanese|belize|belizean|bermuda|caribbean|the caribbean|west indies|fiji
    papua new guinea|malta|maltese|gibraltar|mumbai|bombay|delhi|new delhi|bangalore|bengaluru|chennai|kolkata
    calcutta|hyderabad|pune|kerala|punjab|karachi|lahore|islamabad|dhaka|colombo|lagos|abuja|accra|nairobi|kampala
    johannesburg|joburg|cape town|durban|pretoria|manila|kuala lumpur"""

AMB = """europe|eu|the eu|european|scandinavia|asia|africa|the south|south|the north|north|the east|east|the west|west
    georgia|georgian|montreal|montréal|abroad|overseas|north america|the americas|the continent|latino|latina|hispanic
    southerner|northerner|victorian"""


def _names(text: str) -> list[str]:
    # Same normalisation as locate(): no hyphens, no dots or apostrophes at the ends of words.
    return [" ".join(w.strip(".'") for w in n.split()).replace("-", " ") for n in re.split(r"[|\n]", text) if n.strip()]


PLACES: dict[str, tuple[str, str]] = {}          # name -> (country, class)
for _code, _text in ENG.items():
    for _n in _names(_text):
        PLACES.setdefault(_n, (_code, "ENG"))
for _text in NON.values():
    for _n in _names(_text):
        PLACES.setdefault(_n, (_n, "NON"))
for _n in _names(OUT):
    PLACES.setdefault(_n, (_n, "OUT"))
for _n in _names(AMB):
    PLACES.setdefault(_n, (_n, "AMB"))
# "georgia" is a US state and a country: AMB wins over ENG.
PLACES["georgia"] = ("georgia", "AMB")
# Gulf states, where most residents are expats: being from there says little about the native language.
for _n in ("united arab emirates", "uae", "emirati", "qatar", "qatari", "kuwait", "kuwaiti", "bahrain", "dubai", "abu dhabi"):
    PLACES[_n] = (_n, "OUT")

MAX_WORDS = max(len(k.split()) for k in PLACES)
# ENG names that stand for a whole country (moving there means moving between countries).
ENG_COUNTRIES = set(_names("""us|usa|u.s.|u.s.a.|united states|united states of america|america|the states|states|uk|u.k.
    united kingdom|britain|great britain|england|scotland|wales|northern ireland|ireland|republic of ireland|australia
    aus|oz|new zealand|nz|canada"""))
# Words that may stand before the place in a captured span ("a small town in Ohio", "northern Germany").
LEAD = {"the", "a", "an", "small", "little", "big", "tiny", "rural", "suburban", "downtown", "upstate", "central",
        "north", "south", "east", "west", "northern", "southern", "eastern", "western", "northeast", "northwest",
        "southeast", "southwest", "northeastern", "northwestern", "southeastern", "southwestern", "town", "city",
        "village", "suburb", "suburbs", "part", "area", "region", "province", "state", "county", "countryside",
        "outskirts", "middle", "heart", "edge", "coast", "border", "capital", "of", "in", "near", "outside", "just",
        "around", "very", "beautiful", "sunny"}


def locate(value: str, after: str = "") -> tuple[str, str, str] | None:
    """The place named by a captured span: (name, country, class), or None.
    The place must start the span, possibly after words like "a small town in" or "northern".
    `after` is the text right after the span, to read "U" + ".S." as the US."""
    text = value.lower().replace("’", "'")
    if re.search(r"(?:^|\s)u$", text) and after[:2].lower() in (".s", ".k"):
        text = text[:-1] + ("us" if after[1].lower() == "s" else "uk")
    words = [w.strip(".'") for w in re.findall(r"[\w.'-]+", text)]
    for i in range(len(words)):
        for n in range(min(MAX_WORDS, len(words) - i), 0, -1):
            name = " ".join(words[i:i + n]).replace("-", " ")
            if name in PLACES and not (PLACES[name][1] == "AMB" and words[i + n:i + n + 1] == ["of"]):
                return (name, *PLACES[name])          # "the south of France" goes on to France
        if words[i] not in LEAD:
            return None
    return None

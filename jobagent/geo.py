"""US-or-not location classifier. Returns 'us' | 'non_us' | 'unknown' for messy ATS location strings."""
import re

STATES = {"alabama":"AL","alaska":"AK","arizona":"AZ","arkansas":"AR","california":"CA","colorado":"CO","connecticut":"CT","delaware":"DE",
 "florida":"FL","georgia":"GA","hawaii":"HI","idaho":"ID","illinois":"IL","indiana":"IN","iowa":"IA","kansas":"KS","kentucky":"KY","louisiana":"LA",
 "maine":"ME","maryland":"MD","massachusetts":"MA","michigan":"MI","minnesota":"MN","mississippi":"MS","missouri":"MO","montana":"MT","nebraska":"NE",
 "nevada":"NV","new hampshire":"NH","new jersey":"NJ","new mexico":"NM","new york":"NY","north carolina":"NC","north dakota":"ND","ohio":"OH",
 "oklahoma":"OK","oregon":"OR","pennsylvania":"PA","rhode island":"RI","south carolina":"SC","south dakota":"SD","tennessee":"TN","texas":"TX",
 "utah":"UT","vermont":"VT","virginia":"VA","washington":"WA","west virginia":"WV","wisconsin":"WI","wyoming":"WY","district of columbia":"DC","puerto rico":"PR"}
ABBR = set(STATES.values())
US_CITIES = ["san francisco","mountain view","palo alto","sunnyvale","santa clara","san jose","menlo park","redwood city","foster city","fremont","san mateo",
 "south san francisco","berkeley","oakland","emeryville","alameda","cupertino","milpitas","hayward","san carlos","burlingame","los altos","pleasanton","livermore",
 "seattle","redmond","bellevue","kirkland","boston","cambridge, ma","somerville","waltham","burlington","bedford","lexington","woburn","wilmington, ma","north reading",
 "pittsburgh","austin","houston","dallas","plano","new york","brooklyn","nyc","los angeles","santa monica","pasadena","el segundo","torrance","hawthorne","long beach",
 "irvine","san diego","denver","boulder","golden, co","longmont","detroit","ann arbor","dearborn","troy, mi","auburn hills","chicago","minneapolis","phoenix","tempe",
 "salt lake city","portland","atlanta","miami","orlando","raleigh","durham","charlotte","nashville","philadelphia","washington, dc","washington dc","arlington, va",
 "reston","mclean","huntsville","columbus","cincinnati","cleveland","st. louis","kansas city","milwaukee","madison","indianapolis","louisville","albuquerque",
 "las vegas","reno","sacramento","fresno","salinas","watsonville","davis","santa cruz","san antonio","tucson","peoria","moline","cedar rapids","des moines","omaha",
 "oklahoma city","tulsa","baltimore","laurel","bethesda","rockville","columbia, md","providence","hartford","new haven","stamford","princeton","jersey city",
 "newark","albany","rochester","buffalo","syracuse","ithaca","state college","blacksburg","richmond","norfolk","tampa","jacksonville","cape canaveral","melbourne, fl",
 "boise","spokane","anchorage","honolulu","us remote","remote - us","remote (us)","remote, us","remote us","united states","usa","u.s.","u.s.a"]
NON_US_COUNTRIES = ["united kingdom","uk","england","scotland","wales","ireland","germany","deutschland","france","spain","italy","portugal","netherlands","belgium",
 "switzerland","austria","sweden","norway","denmark","finland","poland","czech","hungary","romania","greece","turkey","israel","india","china","japan","korea",
 "taiwan","singapore","malaysia","thailand","vietnam","indonesia","philippines","australia","new zealand","canada","mexico","brazil","argentina","chile","colombia",
 "south africa","nigeria","kenya","egypt","uae","united arab emirates","saudi","qatar","estonia","latvia","lithuania","ukraine","serbia","croatia","slovakia","slovenia",
 "bulgaria","luxembourg","iceland","hong kong","pakistan","bangladesh","sri lanka","emea","apac","latam","europe"]
NON_US_CITIES = ["london","oxford","cambridge, uk","cambridge, england","manchester","bristol","edinburgh","dublin","paris","toulouse","grenoble","lyon","berlin","munich",
 "münchen","stuttgart","hamburg","frankfurt","karlsruhe","darmstadt","aachen","zurich","zürich","lausanne","geneva","amsterdam","eindhoven","delft","rotterdam",
 "brussels","leuven","stockholm","gothenburg","göteborg","lund","oslo","copenhagen","helsinki","tallinn","warsaw","krakow","wroclaw","prague","vienna","milan","turin",
 "barcelona","madrid","lisbon","tel aviv","haifa","jerusalem","herzliya","bangalore","bengaluru","hyderabad","pune","chennai","mumbai","delhi","gurugram","gurgaon",
 "noida","beijing","shanghai","shenzhen","hangzhou","suzhou","guangzhou","hong kong","tokyo","osaka","seoul","seongnam","daejeon","taipei","hsinchu","singapore",
 "sydney","melbourne, au","melbourne, vic","brisbane","auckland","toronto","vancouver","montreal","montréal","ottawa","waterloo","kitchener","calgary","edmonton",
 "mexico city","guadalajara","monterrey","são paulo","sao paulo","buenos aires","bogota","bogotá","santiago","dubai","abu dhabi","riyadh","cairo","nairobi","lagos"]
CA_PROV = {"ON","BC","QC","AB","MB","SK","NS","NB","NL","PE","YT","NT","NU"}

_st_full = re.compile(r"\b(" + "|".join(re.escape(s) for s in STATES) + r")\b", re.I)
_us_word = re.compile(r"\b(?:USA|U\.S\.A\.?|U\.S\.|United States(?: of America)?)\b", re.I)
_us_short = re.compile(r"(?:^|[,;(\s])US(?:[,;)\s]|$)")

def _one(part):
    p = part.strip(); pl = p.lower()
    if not p: return "unknown"
    if _us_word.search(p) or _us_short.search(p): return "us"
    if any(c in pl for c in NON_US_CITIES): return "non_us"
    # country words at token boundaries
    for c in NON_US_COUNTRIES:
        if re.search(r"(?:^|[,;(\s])" + re.escape(c) + r"(?:[,;)\s]|$)", pl):
            if c == "georgia": continue
            return "non_us"
    toks = [t.strip() for t in re.split(r"[,;/|()\-–]", p)]
    if any(t in CA_PROV for t in toks) and not any(t in ABBR - CA_PROV for t in toks): return "non_us"
    if _st_full.search(p) and not re.search(r"\b(georgia)\b.*\b(tbilisi|caucasus)\b", pl): return "us"
    if any(t in ABBR and t not in ("ON",) for t in toks if len(t) == 2): return "us"
    if any(c in pl for c in US_CITIES): return "us"
    if re.search(r"\b(?:remote|hybrid|anywhere|multiple locations|various)\b", pl): return "unknown"
    return "unknown"

def us_status(location):
    if not location: return "unknown"
    parts = re.split(r"\s*[;|]\s*|\s+or\s+|\s*&\s*", location)
    res = [_one(x) for x in parts if x.strip()]
    if "us" in res: return "us"
    if res and all(r == "non_us" for r in res): return "non_us"
    if "non_us" in res: return "non_us"
    return "unknown"

if __name__ == "__main__":
    for s in ["San Francisco, CA", "US, CA, Santa Clara", "Mountain View, California, USA ", "London, England, United Kingdom", "China, Shanghai; China, Beijing",
              "Toronto, ON", "Cambridge, MA", "Cambridge, UK", "Remote", "Foster City, CA; London", "Pittsburgh, PA, us", "Farmington Hills, MI, us", "Beijing",
              "Hyderabad", "Seattle", "US Remote", "Munich, Germany", "Georgia", "Atlanta, GA", "Waterloo, ON, Canada", "Vancouver, BC", "Austin, TX or Remote", ""]:
        print(f"{us_status(s):8} <- {s!r}")

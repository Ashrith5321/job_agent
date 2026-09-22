"""Relevance scoring & internship classification for robotics / autonomy / perception / ML roles."""
import re

def _rx(pats): return re.compile("|".join(f"(?:{p})" for p in pats), re.I)

INTERN_RE = _rx([r"\bintern(?:s|ship|ships)?\b", r"\bco-?op\b", r"\bcoop\b", r"\bwerkstudent\w*", r"\bpraktik\w+",
                 r"\bworking student\b", r"\b(?:summer|fall|spring|winter)\s*(?:20\d\d|'?\d\d)\b",
                 r"\bstudent (?:researcher|engineer|scientist|assistant|worker|position|program)\b",
                 r"\bphd student\b", r"\b(?:master'?s?|bachelor'?s?) thesis\b", r"\bthesis\b", r"\bstagiaire\b",
                 r"\bstage\b(?= |$)(?!.*\b(?:gate|stage \d)\b)", r"\bsummer (?:analyst|associate|research)\b"])
NEWGRAD_RE = _rx([r"\bnew ?grad\w*", r"\bnew college grad\w*", r"\buniversity grad\w*", r"\bearly[- ]career\b",
                  r"\bentry[- ]level\b", r"\brecent graduate\b", r"\bgraduate (?:engineer|program|programme|scheme|scientist|hire)\b",
                  r"\bcampus\b", r"\bassociate (?:software )?engineer\b", r"\bjunior\b", r"\brotation(?:al)? program\b",
                  r"\bapprentice\w*", r"\bresidency\b", r"\bresident\b(?! (?:director|manager))", r"\bfellow(?:ship)?\b",
                  r"\b(?:20\d\d) (?:grad|start)\b", r"\bnoveau\b", r"\bnew to (?:career|industry)\b"])

GROUPS = {
    "robotics":   (10, [r"robot\w*", r"autonom\w*", r"manipulat\w*", r"humanoid", r"locomotion", r"embodied",
                        r"self[- ]driving", r"\bav\b", r"\badas\b", r"drone", r"\buav\b", r"\buas\b", r"mobile robot",
                        r"legged", r"grasp\w*", r"teleop\w*", r"dexter\w*", r"exoskeleton", r"\brover\b", r"\bsurgical\b"]),
    "planning":   (9,  [r"motion planning", r"path planning", r"\bplanning\b", r"\bplanner\b", r"trajectory",
                        r"behavio(?:u)?r(?:al)? (?:planning|prediction|model)", r"decision[- ]making", r"navigation",
                        r"route planning", r"task planning", r"\bprediction\b"]),
    "perception": (9,  [r"perception", r"computer vision", r"\bvision\b", r"\b3d\b", r"lidar", r"point ?clouds?",
                        r"sensor fusion", r"object detection", r"segmentation", r"\btracking\b", r"depth estimation",
                        r"\bcameras?\b", r"calibration", r"\bslam\b", r"localization", r"\bmapping\b", r"state estimation",
                        r"odometry", r"scene understanding", r"photogrammetry", r"\bnerf\b", r"gaussian splat\w*",
                        r"\bradar\b", r"\bstereo\b", r"multi[- ]?view", r"pose estimation", r"reconstruction",
                        r"occupancy", r"image processing", r"\bimaging\b", r"geometry", r"\bvisual\b"]),
    "ml":         (8,  [r"machine learning", r"deep learning", r"neural", r"foundation model", r"\bvla\b",
                        r"vision[- ]language", r"reinforcement learning", r"imitation learning", r"world model",
                        r"generative", r"diffusion", r"transformer", r"large language", r"\bllm", r"multi-?modal",
                        r"model training", r"\bml\b", r"\bai\b", r"research scientist", r"applied scientist",
                        r"research engineer", r"learning[- ]based", r"end[- ]to[- ]end", r"self[- ]supervised",
                        r"representation learning", r"policy learning", r"behavio(?:u)?r cloning", r"sim[- ]?to[- ]?real",
                        r"\bdata scien\w+", r"\brl\b", r"\bnlp\b", r"inference", r"\bpytorch\b", r"\bjax\b", r"\btensorflow\b"]),
    "controls":   (7,  [r"optimi[sz]ation", r"optimal control", r"\bmpc\b", r"model predictive", r"\bcontrols?\b",
                        r"control systems?", r"\bestimation\b", r"kalman", r"numerical", r"convex", r"\bdynamics\b",
                        r"kinematics", r"\bguidance\b", r"\bgnc\b", r"whole[- ]body", r"\bflight (?:software|control)"]),
    "simulation": (7,  [r"simulat\w*", r"digital twin", r"synthetic data", r"\bisaac\b", r"physics engine",
                        r"mujoco", r"gazebo", r"omniverse", r"unreal", r"\bunity\b", r"scenario generation", r"\bsim\b"]),
    "swe":        (3,  [r"software engineer\w*", r"\bsoftware\b", r"c\+\+", r"\bpython\b", r"\bcuda\b", r"\bros ?2?\b",
                        r"embedded", r"firmware", r"real[- ]time", r"infrastructure", r"\bplatform\b", r"systems? engineer",
                        r"\bgpu\b", r"compiler", r"\bhpc\b", r"\bdeveloper\b", r"\bsde\b", r"\bswe\b", r"algorithm\w*",
                        r"\bresearch\b", r"\bscientist\b", r"\bengineer(?:ing)?\b"]),
}
GROUP_RX = {g: (w, _rx(pats)) for g, (w, pats) in GROUPS.items()}
GROUP_PAT = {g: [re.compile(p, re.I) for p in pats] for g, (w, pats) in GROUPS.items()}

NEGATIVE_TITLE = _rx([
    r"\bsales\b", r"\bmarketing\b", r"\brecruit\w*", r"\btalent\b", r"\baccount (?:executive|manager)\b", r"\bhr\b",
    r"human resources", r"people (?:ops|operations|partner|team)", r"\blegal\b", r"\bcounsel\b", r"paralegal",
    r"\bfinance\b", r"\bfinancial\b", r"accounting", r"accountant", r"\btax\b", r"treasury", r"payroll",
    r"customer (?:success|support|service|experience)", r"business development", r"technical writer", r"copywriter",
    r"content (?:writer|creator|strategist)", r"social media", r"\bcommunications\b", r"public relations",
    r"community manager", r"field service", r"\btechnician\b", r"\bwelder\b", r"machinist", r"\bassembler\b",
    r"warehouse associate", r"forklift", r"\bdriver\b", r"\bjanitor", r"\bfacilities\b", r"receptionist",
    r"executive assistant", r"administrative", r"office manager", r"procurement", r"\bsourcing\b", r"supply chain",
    r"\bbuyer\b", r"it support", r"help ?desk", r"desktop support", r"security guard", r"cyber ?security",
    r"information security", r"\bcompliance\b", r"\baudit\w*", r"\bclinical\b", r"\bnurse\b", r"physician",
    r"\bretail\b", r"\bstore\b", r"barista", r"\bchef\b", r"\bcook\b", r"\bbrand\b", r"\bgrowth\b", r"partnerships",
    r"investor", r"strategic finance", r"public policy", r"government (?:affairs|relations)", r"real estate",
    r"construction project", r"\bevents?\b", r"video producer", r"instructional designer", r"graphic design",
    r"ux (?:writer|researcher)", r"\bui designer\b", r"product designer", r"visual designer", r"industrial designer",
    r"\bcopy\b", r"\beditor\b", r"translator", r"\blinguist\b", r"\bdata entry\b", r"\bpayments?\b", r"\brisk\b",
    r"fraud", r"\btrust (?:and|&) safety", r"\bcontent moderat", r"\bmerchandis", r"\bcategory manager",
    r"\bfleet (?:operations|coordinator)", r"\bdispatch", r"\bcourier", r"\bdelivery (?:associate|driver)",
    r"\bsafety operator\b", r"\btest driver\b", r"\bvehicle operator\b", r"\bautonomous vehicle operator\b",
    r"\bmission specialist\b", r"\btrainer\b", r"\bsecurity officer\b",
])
DOWNWEIGHT_TITLE = _rx([
    r"\bmechanical\b", r"\belectrical\b", r"\bhardware\b", r"manufactur\w*", r"\btest engineer\b", r"quality",
    r"program manager", r"project manager", r"product manager", r"\btpm\b", r"data analyst", r"business analyst",
    r"\bux\b", r"front-?end", r"\bweb\b", r"\bios\b", r"\bandroid\b", r"\bsre\b", r"site reliability",
    r"security engineer", r"\bdevops\b", r"\bit\b", r"\bnetwork\b", r"\bcloud\b", r"\bdata engineer\b",
    r"\banalytics\b", r"\bpcb\b", r"\bfpga\b", r"\basic\b", r"\brf\b", r"\bpower electronics\b", r"\bbattery\b",
    r"\bthermal\b", r"\bstructur", r"\bmaterials?\b", r"\bindustrial engineer", r"\bprocess engineer",
    r"\bfield engineer", r"\bapplications? engineer", r"\bsolutions? engineer", r"\bsupport engineer",
    r"\bintegration engineer", r"\bsystems? integration", r"\bcommissioning", r"\bservice engineer",
    r"\bmechatronic", r"\bactuator", r"\bmotor\b", r"\bsensor (?:hardware|design)", r"\boptical\b", r"\boptics\b",
    r"\bphotonic", r"\bsemiconductor", r"\bpackaging\b", r"\bvalidation\b", r"\bverification\b",
])

HARD_NEGATIVE = _rx([r"\b(?:vehicle|safety|robot|fleet|test|mission|autonomous vehicle|av) (?:operator|driver|specialist|technician|monitor)s?\b",
                     r"\btest drivers?\b", r"\bsafety drivers?\b", r"\btechnicians?\b", r"\btrainers?\b", r"\bdispatch",
                     r"\bremote assistance\b", r"\bteleoperators?\b", r"\bdata (?:labeler|annotator|collector)s?\b",
                     r"\bannotation (?:specialist|associate)", r"\bdata collection (?:specialist|associate|operator)"])

def _hits(text, groups=GROUP_PAT):
    out = {}
    for g, pats in groups.items():
        matched = [p.pattern for p in pats if p.search(text)]
        if matched: out[g] = matched
    return out

# ---------- candidate profile (config/profile.json) ----------
_profile = None
def profile():
    global _profile
    if _profile is None:
        import json
        from .config import CONFIG
        p = CONFIG / "profile.json"
        try: prof = json.loads(p.read_text()) if p.exists() else {}
        except Exception: prof = {}
        prof["_core_rx"] = [re.compile(x, re.I) for x in (prof.get("core_fit") or {}).get("patterns", [])]
        prof["_pen_rx"] = [re.compile(x, re.I) for x in (prof.get("penalty") or {}).get("patterns", [])]
        prof["_core_w"] = float((prof.get("core_fit") or {}).get("_weight", 0)); prof["_pen_w"] = float((prof.get("penalty") or {}).get("_weight", 0))
        _profile = prof
    return _profile

def profile_adjust(base_relevance, title, description="", degree="any", category=None):
    """Re-weights a base relevance (0-100) for this candidate: core-fit keywords up, off-profile keywords down,
    degree-level fit, target vs expired terms, and a small company-category bonus. Returns int 0-100."""
    prof = profile()
    if not prof: return base_relevance
    tl = (title or "").lower(); dl = (description or "")[:8000].lower()
    core_t = sum(1 for rx in prof["_core_rx"] if rx.search(tl)); core_d = sum(1 for rx in prof["_core_rx"] if rx.search(dl))
    pen_t = sum(1 for rx in prof["_pen_rx"] if rx.search(tl)); pen_d = sum(1 for rx in prof["_pen_rx"] if rx.search(dl))
    score = float(base_relevance)
    score += prof["_core_w"] * min(core_t, 3) + prof["_core_w"] * 0.35 * min(core_d, 6)
    score += prof["_pen_w"] * min(pen_t, 2) + prof["_pen_w"] * 0.2 * min(pen_d, 4)
    if core_t == 0 and core_d == 0 and pen_t: score *= 0.5
    score *= float((prof.get("degree_multiplier") or {}).get(degree, 1.0))
    if any(t in tl for t in prof.get("expired_terms", [])): score *= 0.3
    elif any(t in tl for t in prof.get("target_terms", [])): score += 5
    if category: score += float((prof.get("category_bonus") or {}).get(category, 0))
    return int(max(0, min(100, round(score))))

def classify(title, description="", department="", employment_type="", extra_flags=None):
    """Returns dict(is_intern, is_newgrad, relevance(0-100), keywords(list), excluded(bool), reason)."""
    title = title or ""
    tl = title.lower()
    meta = " ".join(x for x in (department, employment_type) if x).lower()
    extra_flags = [str(x).lower() for x in (extra_flags or [])]
    is_intern = bool(INTERN_RE.search(tl) or INTERN_RE.search(meta) or any("intern" in f for f in extra_flags))
    is_newgrad = bool(NEWGRAD_RE.search(tl) or NEWGRAD_RE.search(meta) or any(("grad" in f or "entry" in f) for f in extra_flags))
    # "Intern" in employment_type but title says senior/staff -> trust title
    if is_intern and re.search(r"\b(senior|staff|principal|lead|director|manager|head of)\b", tl) and not INTERN_RE.search(tl):
        is_intern = False
    if HARD_NEGATIVE.search(tl) or (NEGATIVE_TITLE.search(tl) and not re.search(r"\b(robot|perception|autonom|planning|vision|learning|simulation|control)", tl)):
        return {"is_intern": is_intern, "is_newgrad": is_newgrad, "relevance": 0, "keywords": [], "excluded": True, "reason": "non-technical title"}
    th = _hits(tl)
    desc = (description or "")[:8000].lower()
    dh = _hits(desc) if desc else {}
    tscore = sum(GROUPS[g][0] for g in th)
    # description: weight by number of distinct patterns matched per group, saturating
    dscore = 0.0
    for g, pats in dh.items():
        w = GROUPS[g][0]
        dscore += w * min(1.0, 0.35 + 0.15 * len(pats))
    raw = tscore * 3 + dscore * 0.5
    if DOWNWEIGHT_TITLE.search(tl) and not any(g in th for g in ("robotics", "planning", "perception", "ml", "simulation", "controls")):
        raw *= 0.45
    elif DOWNWEIGHT_TITLE.search(tl):
        raw *= 0.75
    if not th and not dh:
        raw = 0
    relevance = int(min(100, round(raw * 100 / 60.0)))
    kws = []
    for g in ("robotics", "planning", "perception", "ml", "controls", "simulation", "swe"):
        if g in th: kws.append(g + "*")
        elif g in dh: kws.append(g)
    return {"is_intern": is_intern, "is_newgrad": is_newgrad, "relevance": relevance, "keywords": kws, "excluded": False, "reason": ""}

PHD_RE = _rx([r"\bph\.?d\b", r"\bdoctora(?:l|te)\b", r"\bpost-?doc\w*"])
MS_RE = _rx([r"\bm\.?s\.?(?:c)?\b", r"\bmaster'?s?\b", r"\bmeng\b", r"\bm\.eng\b", r"\bgraduate student\b", r"\bgrad student\b"])
BS_RE = _rx([r"\bb\.?s\.?(?:c)?\b", r"\bbachelor'?s?\b", r"\bundergrad\w*", r"\bb\.?eng\b", r"\bbe\b(?=\s*/)", r"\bbtech\b"])
_DESC_PURSUING = re.compile(r"(?:pursuing|enrolled in|working toward[s]?|candidate for|currently (?:in|a)|student in)\s+(?:an?\s+)?([^.\n]{0,80})", re.I)

def degree_level(title, description=""):
    """Returns one of: 'phd', 'ms', 'bs', 'ms/phd', 'bs/ms', 'bs/ms/phd', 'any'. Title wins; description is consulted only if title says nothing."""
    def scan(text):
        t = text or ""
        return {"phd": bool(PHD_RE.search(t)), "ms": bool(MS_RE.search(t)), "bs": bool(BS_RE.search(t))}
    f = scan(title)
    if not any(f.values()):
        d = (description or "")[:6000]
        # look at "pursuing a ..." sentences first, then the whole first 6k chars
        segs = " ".join(m.group(1) for m in _DESC_PURSUING.finditer(d)) or d
        f = scan(segs)
        if not any(f.values()) and segs is not d: f = scan(d)
    lv = [k for k in ("bs", "ms", "phd") if f[k]]
    return "/".join(lv) if lv else "any"

def quick_title_interest(title, department="", employment_type=""):
    """Cheap pre-filter used before fetching job details: True if title looks like an intern/new-grad role
    OR mentions any core domain keyword."""
    tl = (title or "").lower()
    meta = " ".join(x for x in (department, employment_type) if x).lower()
    if HARD_NEGATIVE.search(tl): return False
    if NEGATIVE_TITLE.search(tl) and not re.search(r"\b(robot|perception|autonom|planning|vision|learning|simulation|control)", tl):
        return False
    if INTERN_RE.search(tl) or INTERN_RE.search(meta) or NEWGRAD_RE.search(tl) or NEWGRAD_RE.search(meta):
        return True
    for g in ("robotics", "planning", "perception", "ml", "controls", "simulation"):
        if GROUP_RX[g][1].search(tl): return True
    return False

if __name__ == "__main__":
    tests = [
        ("Robotics Software Engineer Intern", "We build humanoid robots. You will work on perception, motion planning, C++ and ML.", "", "Intern"),
        ("Perception Engineer, Autonomous Vehicles", "lidar camera fusion, deep learning", "", "Full-time"),
        ("Software Engineer Intern", "Build web services.", "", ""),
        ("Strategic Finance Intern - 2027", "", "", ""),
        ("Mechanical Engineer Intern, Robotics", "", "", ""),
        ("Machine Learning Engineer", "training transformers for robot policies", "", ""),
        ("2027 Summer Intern, BS/MS, Pipeline and Test Health Engineer", "autonomous driving", "", ""),
        ("Research Scientist Intern, Optical System Design (PhD)", "", "", ""),
        ("Senior Motion Planning Engineer", "", "", "Intern"),
        ("Autonomous Vehicle Operator", "", "", ""),
    ]
    for t in tests:
        print(f"{classify(*t)}  <- {t[0]}")

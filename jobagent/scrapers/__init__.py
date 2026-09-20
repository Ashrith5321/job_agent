from .greenhouse import Greenhouse
from .lever import Lever
from .ashby import Ashby
from .smartrecruiters import SmartRecruiters
from .workday import Workday
from .amazon import Amazon
from .apple import Apple
from .linkedin import LinkedInCompany, LinkedInSearch
from .handshake import Handshake
from .misc_ats import Workable, Recruitee, BambooHR, Rippling, Personio, Teamtailor

REGISTRY = {c.provider: c for c in (Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Amazon, Apple,
                                    LinkedInCompany, LinkedInSearch, Handshake, Workable, Recruitee, BambooHR, Rippling, Personio, Teamtailor)}

# Providers the prober tries by slug (order = likelihood in the robotics space)
PROBEABLE = ["greenhouse", "lever", "ashby", "smartrecruiters", "workable", "recruitee", "bamboohr", "rippling", "personio", "teamtailor"]

def get(provider):
    cls = REGISTRY.get(provider)
    return cls() if cls else None

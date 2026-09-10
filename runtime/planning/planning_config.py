"""Central planning-system configuration for Wacky Dramas.

Keep strategy knobs here so planner behavior is auditable and tests can assert the
production policy without scattering magic numbers across the codebase.
"""

DAILY_PUBLISH_COUNT = 24
RAW_CANDIDATE_COUNT = 120
SEMIFINALIST_TARGET = 36
TITLES_PER_SEMIFINALIST = 5
EXPLORATION_FRACTION = 0.20
CANONICAL_TIMEZONE = "Asia/Singapore"

EDITORIAL_WEIGHTS = {
    "opening_hook_potential": 20,
    "curiosity_gap": 20,
    "emotional_stakes": 15,
    "escalation_potential": 10,
    "payoff_quality": 10,
    "title_potential": 10,
    "broad_relatability": 5,
    "originality": 5,
    "narration_suitability": 5,
}

TITLE_WEIGHTS = {
    "curiosity_gap": 30,
    "emotional_impact": 20,
    "unanswered_question": 15,
    "immediate_comprehension": 10,
    "specificity": 10,
    "natural_phrasing": 5,
    "conciseness": 5,
    "truthful_reflection": 5,
}

HOOK_WEIGHTS = {
    "conflict_speed": 25,
    "unanswered_question": 25,
    "adds_beyond_title": 20,
    "context_efficiency": 15,
    "spoken_naturalness": 10,
    "immediate_comprehension": 5,
}

# Raw YouTube metrics are normalized against age-matched Wacky Dramas cohorts
# before they influence future candidates. These weights describe the historical
# row score, not the final editorial/analytics blend.
RAW_ANALYTICS_WEIGHTS = {
    "engaged_view_rate": 20,
    "average_percentage_viewed": 20,
    "net_subscribers_per_1000_views": 20,
    "shares_per_1000_views": 15,
    "qualified_shorts_views": 10,
    "likes_per_1000_views": 7.5,
    "comments_per_1000_views": 7.5,
}

# Candidate analytics enters planning only through the normalized historical
# attribute-fit score produced by analytics_learning.py.

DIVERSITY_LIMITS = {
    "category": 4,
    "conflict": 2,
    "title_style": 3,
}

CATEGORIES = {
    "RELATIONSHIP", "DATING", "MARRIAGE", "BETRAYAL", "FAMILY", "INHERITANCE",
    "MONEY", "WORKPLACE", "REVENGE", "FRIENDSHIP", "WEDDING", "ENTITLED_PERSON",
    "NEIGHBOR", "SECRETS", "DISCOVERY", "SOCIAL_CONFLICT", "PROPERTY", "PARENTING",
    "MORAL_DILEMMA", "KINDNESS", "MISUNDERSTANDING", "HIDDEN_IDENTITY", "CONSEQUENCES",
    "TWIST", "WILDCARD",
}

EMOTIONS = {
    "ANGER", "BETRAYAL", "INJUSTICE", "EMBARRASSMENT", "FEAR", "DISBELIEF", "REVENGE",
    "LOVE", "GUILT", "REGRET", "SURPRISE", "RELIEF", "HOPE", "SADNESS", "JOY",
}

OPENING_STYLES = {
    "IMMEDIATE_REVELATION", "CONTRADICTION", "DISCOVERY", "CONSEQUENCE_FIRST",
    "URGENT_CONFLICT", "SHOCKING_STATEMENT", "UNANSWERED_EVENT", "DECISION_FIRST",
}

TITLE_STYLES = {
    "HIDDEN_REVELATION", "DISCOVERY", "NORMAL_TO_ABNORMAL", "DECISION_CONSEQUENCE",
    "COUNTDOWN", "UNDERESTIMATED_NARRATOR", "MORAL_CONFLICT", "DELAYED_REVELATION",
    "CONTRADICTION", "CONSEQUENCE_FIRST",
}

ENDING_STYLES = {
    "REVERSAL", "BACKFIRE", "REVEAL", "CONSEQUENCE", "RECONCILIATION", "BOUNDARY_SET",
    "JUSTICE", "SACRIFICE", "KINDNESS_RETURNED", "BITTERSWEET", "OPEN_RESOLUTION",
}

PROTAGONIST_ROLES = {
    "PARTNER", "SPOUSE", "EMPLOYEE", "MANAGER", "SIBLING", "PARENT", "ADULT_CHILD",
    "FRIEND", "ROOMMATE", "NEIGHBOR", "TENANT", "LANDLORD", "CUSTOMER", "OWNER",
    "BRIDE", "GROOM", "STUDENT", "TEACHER", "TRAVELER", "STRANGER", "RELATIVE",
}

ANTAGONIST_ROLES = PROTAGONIST_ROLES | {"COWORKER", "BOSS", "EX_PARTNER", "VENDOR", "GUEST"}

MIN_FINAL_EDITORIAL_SCORE = 68.0
MIN_TITLE_SCORE = 70.0
MIN_HOOK_SCORE = 70.0
NEAR_DUPLICATE_THRESHOLD = 0.82

# Analytics must mature before it can steer creative selection.
# analytics_evidence_count in analytics/latest.json is an evidence-equivalent count:
# it is zero until >=10 videos have a 24h cohort snapshot, then is capped by
# both mature video count and one evidence unit per 500 comparable views.
MAX_ANALYTICS_WEIGHT = 0.60
ANALYTICS_CONFIDENCE_SCALE = 50.0
ANALYTICS_MIN_MATURE_VIDEOS = 10
ANALYTICS_VIEWS_PER_EVIDENCE_UNIT = 500
ANALYTICS_MATURITY_HOURS = 24
ANALYTICS_ATTRIBUTE_PRIOR_STRENGTH = 4.0
MILESTONE_CAPTURE_TOLERANCE_HOURS = 6.5

MILESTONE_HOURS = {
    "24h": 24,
    "72h": 72,
    "7d": 168,
}


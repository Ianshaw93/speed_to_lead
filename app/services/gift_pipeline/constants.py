"""Constants for the gift leads pipeline."""

# Apify Actor IDs
GOOGLE_SEARCH_ACTOR = "nFJndFXA5zjCTuudP"
POST_REACTIONS_ACTOR = "J9UfswnR3Kae4O6vm"
PROFILE_SCRAPER_ACTOR = "supreme_coder~linkedin-profile-scraper"

# Apify cost estimates (USD per unit)
APIFY_COSTS = {
    "google_search": 0.004,      # ~$0.004 per search result
    "post_reactions": 0.008,     # ~$0.008 per post scraped
    "profile_scraper": 0.004,    # ~$0.004 per profile ($3.67/1K)
}

# DeepSeek pricing (USD per 1M tokens)
DEEPSEEK_COSTS = {
    "input_per_1m": 0.14,
    "output_per_1m": 0.28,
    "avg_icp_tokens": 400,
    "avg_personalization_tokens": 800,
}

# Default allowed countries for location filtering
DEFAULT_COUNTRIES = ["United States", "Canada", "USA", "America"]

# Headline authority keywords (positive signal)
HEADLINE_AUTHORITY_KEYWORDS = [
    "ceo", "founder", "co-founder", "cofounder", "owner",
    "president", "managing director", "partner",
    "vp", "vice president", "director",
    "cto", "cfo", "coo", "cmo", "chief",
    "head of", "principal", "entrepreneur",
]

# Hard rejection keywords
HEADLINE_REJECT_KEYWORDS = [
    "intern", "student", "trainee", "apprentice",
    "cashier", "driver", "technician", "mechanic",
    "nurse", "teacher", "professor", "doctor", "physician",
    "looking for", "seeking", "open to work",
    "retired", "unemployed",
]

# Non-English indicators in headlines
NON_ENGLISH_INDICATORS = [
    # Portuguese
    "diretor", "gerente", "fundador", "empresário", "sócio", "coordenador",
    # Spanish
    "gerente", "fundador", "empresario", "socio", "coordinador",
    # French
    "directeur", "fondateur", "gérant", "président", "responsable",
    # German
    "geschäftsführer", "gründer", "leiter", "inhaber",
    # Italian
    "direttore", "fondatore", "titolare", "amministratore",
    # Dutch
    "directeur", "oprichter", "eigenaar",
]

# Placeholder headlines that indicate empty/incomplete profiles
EMPTY_HEADLINE_INDICATORS = ["--", "n/a", "na", "-", ""]

# Pipeline defaults
DEFAULT_DAYS_BACK = 14
DEFAULT_MIN_REACTIONS = 50
DEFAULT_MIN_LEADS = 10
DEFAULT_MAX_LEADS = 25
PROFILE_BATCH_SIZE = 100
SIGNAL_NOTE_BATCH_SIZE = 10

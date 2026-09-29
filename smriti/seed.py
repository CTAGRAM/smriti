"""Demo state: one village, two health workers, a district server.
Protocol texts are short sample summaries for the demo, not medical guidance."""

PROTOCOLS = [
    ("Danger signs in pregnancy", "Refer immediately to the nearest facility if there is vaginal bleeding, severe headache with blurred vision, convulsions, high fever, severe abdominal pain, swelling of face and hands, or reduced baby movements."),
    ("Antenatal check-ups", "At least four antenatal check-ups, the first in the first trimester. Give iron-folic acid daily and track blood pressure and weight at every visit."),
    ("Newborn danger signs", "Refer a newborn who is not feeding well, has convulsions, fast breathing, chest indrawing, fever or low temperature, or yellow palms and soles."),
    ("Low birth weight care", "For babies under 2.5 kg: kangaroo mother care, frequent breastfeeding, keep warm, and extra home visits."),
    ("Home visits after birth", "Visit the newborn at home on days 3, 7, 14, 21, 28 and 42. Check feeding, temperature, cord and weight."),
    ("Diarrhoea in children", "Give ORS after every loose stool and zinc for 14 days. Refer if the child is lethargic, cannot drink, has blood in stool, or sunken eyes."),
    ("Fever during monsoon", "Suspect malaria or dengue. Arrange a rapid test and refer if the fever lasts more than two days or there is bleeding or severe weakness."),
    ("Immunisation reminders", "Measles-rubella vaccine is due at 9 months. Check the card at every visit and list children who have missed doses."),
]

ASHA = ("asha-sunita", "Sunita (ASHA)", "ASHA · Rampur village · phone")
ANM = ("anm-rekha", "Rekha (ANM)", "ANM · sub-centre · tablet")

REGISTERS = {
    "asha-sunita": {"Meena": "HH-014", "Anita": "HH-011", "Kavita": "HH-017", "Pooja": "HH-021", "Radha": "HH-008"},
    "anm-rekha": {"Meena": "HH-014", "Pooja": "HH-021", "Geeta": "HH-032", "Lakshmi": "HH-035"},
}

HOUSEHOLDS = {
    "HH-014": {"name": "Meena", "status": "pregnant, 7 months", "risk": "normal", "next_visit": "3 Oct", "notes": "second pregnancy"},
    "HH-011": {"name": "Anita", "status": "newborn, 9 days, 2.1 kg", "risk": "watch: low birth weight", "next_visit": "1 Oct (day 14 visit)"},
    "HH-017": {"name": "Kavita", "status": "child 2 yrs, loose stools since yesterday", "risk": "watch", "next_visit": "today"},
    "HH-021": {"name": "Pooja", "status": "pregnant, 4 months", "risk": "normal", "next_visit": "12 Oct (2nd check-up)"},
    "HH-008": {"name": "Radha", "status": "child 9 months, measles-rubella vaccine due", "risk": "normal", "next_visit": "5 Oct"},
}

ASHA_NOTES = [
    ("Anita's baby weighed 2.1 kg on day 7, breastfeeding well; advised kangaroo care and keeping the baby warm", "HH-011", False),
    ("Kavita's son had loose stools four times since morning; gave ORS and zinc, will check again tomorrow", "HH-017", False),
    ("Pooja got iron tablets after her first check-up at the PHC; next visit in October", "HH-021", False),
    ("Personal: buy new batteries for the BP machine", None, True),
]

ANM_NOTES = [
    ("Sonpur: two fever cases this week near the pond; advised malaria rapid test", None, False),
]

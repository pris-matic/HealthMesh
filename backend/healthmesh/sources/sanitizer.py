"""Conservative demo privacy gate; original bytes never enter the AMD view."""
import re

from django.core.exceptions import ValidationError

# Reject patient/clinical material even when it is labelled synthetic. This demo
# accepts administrative stock reports only, not de-identified health records.
SENSITIVE = re.compile(
    r'\b(?:patient(?:[ _-]*(?:name|id))?|mrn|medical[ _-]*record(?:[ _-]*number)?|'
    r'date[ _-]*of[ _-]*birth|dob|diagnosis|diagnoses|prescription|dosage|'
    r'blood[ _-]*pressure|chief[ _-]*complaint|ssn|social[ _-]*security|'
    r'home[ _-]*address|street[ _-]*address|staff[ _-]*name|full[ _-]*name|'
    r'allerg(?:y|ies)|diabetes|insulin|hypertension|symptoms|treatment)\b', re.I)
PERSONAL_FIELD = re.compile(r'\b(?:name|address|birth[ _-]*date|national[ _-]*id)\s*[:=]', re.I)
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}')
PHONE = re.compile(r'(?<!\w)(?:\+\d{1,3}[ .-]?)?(?:\(?\d{3}\)?[ .-])\d{3}[ .-]\d{4}(?!\w)')


def sanitize(text):
    if SENSITIVE.search(text) or PERSONAL_FIELD.search(text):
        raise ValidationError({'file': 'Personal or clinical data is prohibited. Use synthetic administrative supply data only.'})
    for pattern in (EMAIL, PHONE):
        text = pattern.sub(lambda match: '*' * len(match.group()), text)
    return text

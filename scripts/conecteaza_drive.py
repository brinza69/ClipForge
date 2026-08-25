r"""Conecteaza Google Drive + Sheets pe un aparat care doar posteaza.

Pe rigul de randare conectarea se face din interfata ClipForge. Un laptop care
doar distribuie n-are backend — `services/drive_oauth.py` cere `config.settings`,
adica tot stack-ul FastAPI, care nu e in requirements-postare.txt. Deci fluxul
sta aici, de sine statator: patru pachete si un browser.

Ce trebuie sa existe deja: `data/drive_oauth_client.json` (clientul OAuth de tip
Desktop, copiat de pe rig). Tokenul NU se copiaza — il face scriptul asta, local.

    python scripts\conecteaza_drive.py

Se deschide browserul, aprobi cu contul care detine folderele, si tokenul se
scrie in `data/drive_oauth_token.json`.

Aplicatia OAuth e in modul Testing, deci **tokenul expira cam saptamanal**. Cand
apar 401/403 pe Drive sau Sheets, se ruleaza din nou. (Un 503 NU e asta — ala e
Google care are o zi proasta.)
"""
import pathlib
import sys

_ROOT = pathlib.Path(__file__).resolve().parents[1]
CLIENT = _ROOT / "data" / "drive_oauth_client.json"
TOKEN = _ROOT / "data" / "drive_oauth_token.json"

# Aceleasi scopuri ca backendul, altfel tokenul nu e bun pentru Sheets.
SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]
# Portul de loopback e dedicat: 8420/8421 sunt backendurile de pe rig.
PORT = 8765

if not CLIENT.exists():
    sys.exit(f"lipseste {CLIENT}\n"
             "E clientul OAuth de tip Desktop — cere-l de pe rigul de randare.")

from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: E402

flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), scopes=SCOPES)
print("Se deschide browserul. Aproba cu contul care detine folderele de Drive.")
creds = flow.run_local_server(port=PORT, prompt="consent",
                              authorization_prompt_message="Deschide: {url}")
TOKEN.parent.mkdir(parents=True, exist_ok=True)
TOKEN.write_text(creds.to_json(), encoding="utf-8")
print(f"scris: {TOKEN}")

# Verificare imediata: un token care nu poate citi sheet-ul nu e de niciun folos.
try:
    from googleapiclient.discovery import build
    sys.path.insert(0, str(_ROOT / "scripts"))
    import targets
    s = build("sheets", "v4", credentials=creds, cache_discovery=False)
    r = s.spreadsheets().values().get(
        spreadsheetId=targets.get("pov_sheet_id"),
        range=f"'{targets.get('pov_tab', 'Sheet1')}'!A1:A3").execute()
    print(f"verificat: sheet-ul raspunde ({len(r.get('values', []))} randuri citite)")
except Exception as e:  # noqa: BLE001
    print(f"ATENTIE: tokenul s-a scris, dar citirea de proba a picat: {str(e)[:140]}")
    print("Daca e 403, contul aprobat nu are acces la sheet. Daca e 503, e Google — reincearca.")

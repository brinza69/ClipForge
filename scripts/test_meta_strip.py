"""Unit-test _strip_meta_commentary against the leaked output from the
hisytstory test run."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
from services.transcript_cleaner import _strip_meta_commentary  # noqa: E402

# This is the actual cleaned_text returned by Ollama on the user's Grinch
# TikTok run — duplicated content + meta-commentary.
SAMPLE = """I conduc o drum când văd o bicicletă de copil pe drumul meu. Îi întreb politoarele copil cu balonul să meargă, dar el mi-a zis: "Zâmbiți-vă, vechiule." Mama a intervenit: "Da, l-ai primit la Crăciun." Am rămas încântat și am pus pedalile de accelerație pe maxim.

---

Pentru a fi mai natural în limba română, textul ar putea fi ajustat astfel:

Conduc o drum când văd o bicicletă de copil pe drumul meu. Îi întreb politoarele copil cu balonul să meargă. Mama a intervenit: "Da, l-ai primit la Crăciun."

---

Acesta este un text care respectă toate cerințele date și rămâne aproape la lungimea originalului."""

cleaned = _strip_meta_commentary(SAMPLE)
print("=" * 60)
print(f"INPUT length:  {len(SAMPLE):>5} chars")
print(f"OUTPUT length: {len(cleaned):>5} chars")
print(f"Reduction:     {(1 - len(cleaned)/len(SAMPLE)) * 100:.1f}%")
print("=" * 60)
print("OUTPUT:")
print(cleaned)
print("=" * 60)

"""Akıllı kategori tespiti.

Kurallar:
  * Anahtar kelimeler Türkçe karakterlerden arındırılmış (fold) halde yazılır.
  * "tarih*"  -> kelime başı eşleşmesi (tarihi, tarihçe, tarihin ...)
  * "din"     -> yalnızca tam kelime (dinamik, Edinburgh eşleşmez)
  * "islam felsefe*" -> ardışık kelime grubu
  * "authors" -> yalnızca yazar alanında aranır.
Puanlama: başlık/konu etiketi eşleşmesi 3, klasör adı 2, yazar 3 puan.
"""
from .textutil import words

CATEGORIES = [
    ("Din", "Kur'an ve Tefsir", {
        "kw": ["القران", "القرآن", "تفسير", "kuran*", "kur'an*", "tefsir*", "meal*", "mushaf*", "quran*", "koran*", "tecvid*",
               "ulumul kuran", "esbab-i nuzul", "tafsir*"],
        "authors": ["elmalili", "taberi", "kurtubi", "ibn kesir", "razi", "zemahseri", "beyzavi"]}),
    ("Din", "Hadis ve Siyer", {
        "kw": ["حديث", "الحديث", "السيرة", "صحيح", "hadis*", "sunnet*", "siyer*", "peygamber*", "hz", "hazret*", "muhammed*", "buhari*",
               "muslim", "sahih*", "riyazu*", "hadith*", "sunan", "musned*", "sirah", "megazi"],
        "authors": ["buhari", "muslim", "tirmizi", "ebu davud", "nesai", "ibn mace", "nevevi"]}),
    ("Din", "Fıkıh ve İlmihal", {
        "kw": ["فقه", "الفقه", "fikih*", "fikh*", "fiqh*", "ilmihal*", "fetva*", "usul*", "mecelle*", "mezhep*",
               "mezheb*", "hanefi*", "safii*", "maliki*", "hanbeli*", "ibadet*", "namaz*", "oruc*",
               "zekat*", "hac", "miras", "feraiz*", "hidaye*"],
        "authors": ["serahsi", "kasani", "ibn abidin", "merginani"]}),
    ("Din", "Akaid ve Kelam", {
        "kw": ["akaid*", "akide*", "itikad*", "itikat*", "kelam*", "tevhid*", "iman*", "aqidah",
               "maturidi*", "esari*", "esariyye"],
        "authors": ["maturidi", "esari", "nesefi", "teftazani", "cuveyni"]}),
    ("Din", "Tasavvuf", {
        "kw": ["تصوف", "التصوف", "tasavvuf*", "sufi*", "tarikat*", "mesnevi*", "zikir*", "evliya*", "velayet*",
               "marifet*", "mektubat*", "fususul*", "fusus*", "futuhat*", "dergah*", "seyr-i suluk",
               "sufism"],
        "authors": ["mevlana", "rumi", "muhyiddin", "ibn arabi", "ibnul arabi", "yunus emre",
                    "kuseyri", "imam rabbani", "hucviri", "attar", "niyazi misri", "aziz mahmud hudayi"]}),
    ("Din", "Risale-i Nur", {
        "kw": ["risale*", "sozler", "mektubat", "lemalar", "sualar", "nursi*"],
        "authors": ["said nursi", "bediuzzaman", "said-i nursi"]}),
    ("Din", "İslam Genel", {
        "kw": ["islam*", "ilahiyat*", "muslumanlik", "musluman*", "ahlak*", "dua*", "cami*",
               "imam", "ulema*", "alim*", "ilm*", "muslim world", "islamic"],
        "authors": ["الغزالي", "ابن تيمية", "ابن القيم", "gazali", "gazzali", "ibn teymiyye", "ibn kayyim", "hamidullah", "nurettin topcu"]}),
    ("Din", "Diğer Dinler", {
        "kw": ["yahudi*", "hristiyan*", "hiristiyan*", "incil*", "tevrat*", "kilise*", "musevi*",
               "bible", "christian*", "jewish", "judaism", "budizm*", "hinduizm*", "dinler tarihi",
               "religion*", "theology", "teoloji*", "din", "dini", "dinler*", "dinin", "dinde"],
        "authors": []}),

    ("Felsefe", "İslam Felsefesi", {
        "kw": ["فلسفة", "الفلسفة", "islam felsefe*", "mesai*", "isrâki*", "israki*", "falsafa"],
        "authors": ["الفارابي", "ابن سينا", "ابن رشد", "الكندي", "ابن خلدون", "farabi", "ibn sina", "ibni sina", "ibn rusd", "ibni rusd", "kindi",
                    "ibn tufeyl", "suhreverdi", "molla sadra", "ibn haldun", "ibni haldun"]}),
    ("Felsefe", "Mantık", {
        "kw": ["منطق", "المنطق", "mantik*", "logic*", "kiyas*", "isagoci*", "burhan*"],
        "authors": []}),
    ("Felsefe", "Genel Felsefe", {
        "kw": ["felsefe*", "philosoph*", "etik*", "ahlak felsefe*", "metafizik*", "epistemoloji*",
               "ontoloji*", "varlik*", "hikmet*", "dusunce*", "fenomenoloji*", "varolus*",
               "ethics", "metaphysic*", "filosofie"],
        "authors": ["platon", "eflatun", "aristo", "aristoteles", "kant", "hegel", "nietzsche",
                    "heidegger", "descartes", "spinoza", "sokrates", "wittgenstein", "bergson",
                    "husserl", "kierkegaard", "schopenhauer", "rene guenon", "guenon"]}),

    ("Tarih", "Osmanlı Tarihi", {
        "kw": ["osmanli*", "padisah*", "sadrazam*", "devlet-i aliyye", "tanzimat*", "mesrutiyet*",
               "yeniceri*", "sultan*", "ottoman*", "tarih-i*", "vekayi*", "sefaretname*", "lale devri"],
        "authors": ["ahmet cevdet", "cevdet pasa", "naima", "halil inalcik", "inalcik", "peçevi",
                    "pecevi", "asikpasazade", "ilber ortayli"]}),
    ("Tarih", "İslam Tarihi", {
        "kw": ["islam tarih*", "hilafet*", "halife*", "emevi*", "abbasi*", "endulus*", "selcuk*",
               "haclı", "hacli*", "asr-i saadet*", "sahabe*"],
        "authors": ["taberi", "ibn esir", "belazuri", "hamidullah"]}),
    ("Tarih", "Türk ve Dünya Tarihi", {
        "kw": ["تاريخ", "التاريخ", "tarih*", "history", "histories", "historical", "savas*", "imparatorluk*", "devrim*",
               "antik*", "ortacag*", "medeniyetler tarih*", "cumhuriyet*", "geschiedenis",
               "geschichte", "war", "empire*", "kronik*"],
        "authors": []}),

    ("Sosyoloji", "Toplum ve Kültür", {
        "kw": ["sosyoloji*", "sociolog*", "toplum*", "kultur*", "medeniyet*", 
               "kentlesme*", "modernlesme*", "modernite*", "sekulerlesme*", "aile*", "society",
               "culture*", "civilization*", "antropoloji*", "anthropolog*", "umran*", "mukaddime*"],
        "authors": ["ibn haldun", "durkheim", "weber", "marx", "cemil meric", "serif mardin",
                    "ali sariati", "bourdieu", "baudrillard", "zygmunt bauman", "bauman"]}),
    ("Sosyoloji", "Siyaset", {
        "kw": ["siyaset*", "politika*", "politic*", "devlet", "demokrasi*", "ideoloji*",
               "hukuk*", "anayasa*", "milliyetcilik*", "liberalizm*", "sosyalizm*"],
        "authors": ["machiavelli", "hobbes", "locke", "rousseau", "montesquieu"]}),

    ("Psikoloji", "Psikoloji ve Gelişim", {
        "kw": ["psikoloji*", "psycholog*", "bilinc*", "terapi*", "davranis*", "kisisel gelisim*",
               "zihin*", "nefs", "ruh sagli*", "psychologie", "mindset", "habit*", "aliskanlik*"],
        "authors": ["freud", "jung", "adler", "fromm", "frankl", "erich fromm"]}),

    ("Edebiyat", "Divan ve Klasik Edebiyat", {
        "kw": ["divan*", "gazel*", "kaside*", "mesnevi", "siir*", "nazim", "beyit*", "saray edebiyat*",
               "klasik edebiyat*"],
        "authors": ["fuzuli", "baki", "nedim", "seyh galib", "nefi", "nabi", "yunus emre"]}),
    ("Edebiyat", "Roman ve Hikaye", {
        "kw": ["roman*", "hikaye*", "oyku*", "novel*", "story", "stories", "poem*", "poetry",
               "edebiyat*", "literature", "deneme*", "masal*"],
        "authors": ["flaubert", "dostoyevski", "tolstoy", "sait faik", "sabahattin ali",
                    "peyami safa", "necip fazil", "sezai karakoc", "tanpinar", "orhan pamuk"]}),
    ("Edebiyat", "Fantastik ve Bilim Kurgu", {
        "kw": ["bilim kurgu*", "sci-fi", "science fiction", "fantastik*", "fantasy", "galaksi*",
               "uzay*"],
        "authors": ["tolkien", "asimov", "philip k dick", "ursula le guin"]}),

    ("Dil ve Sözlük", "Dil, Gramer, Sözlük", {
        "kw": ["lugat*", "lugati", "sozluk*", "gramer*", "dilbilgisi*", "sarf", "nahiv*",
               "elifba*", "arapca*", "farsca*", "osmanlica*", "dictionary", "grammar",
               "woordenboek*", "kamus*", "kamusu"],
        "authors": ["semseddin sami", "redhouse"]}),

    ("Bilim ve Teknoloji", "Yazılım ve Bilgisayar", {
        "kw": ["python", "docker", "linux", "programming", "programlama*", "algoritma*",
               "software", "javascript", "react", "git", "github", "bilgisayar*", "computer*",
               "yapay zeka*", "machine learning", "veri*", "kubernetes", "sql", "network*"],
        "authors": []}),
    ("Bilim ve Teknoloji", "Fen Bilimleri", {
        "kw": ["fizik*", "kimya*", "biyoloji*", "matematik*", "astronomi*", "evrim*",
               "physics", "chemistry", "biology", "mathemat*", "tip", "anatomi*"],
        "authors": ["einstein", "hawking", "darwin"]}),

    ("Ekonomi ve İş", "Ekonomi ve Finans", {
        "kw": ["ekonomi*", "economy", "economic*", "finans*", "finance", "pazarlama*",
               "marketing", "yonetim*", "business", "economie", "girisim*", "yatirim*",
               "management", "leadership", "liderlik*", "satis*", "sales"],
        "authors": ["adam smith", "keynes", "drucker"]}),
]

# Ön derleme: her anahtar kelime -> (kelime listesi, son kelime prefix mi)
_COMPILED = []
for main, sub, spec in CATEGORIES:
    kws = []
    for kw in spec["kw"]:
        prefix = kw.endswith("*")
        toks = words(kw.rstrip("*").replace("-", " "))
        if toks:
            kws.append((toks, prefix))
    authors = [words(a.replace("-", " ")) for a in spec["authors"]]
    _COMPILED.append((main, sub, kws, [a for a in authors if a]))


def _match(tokens, kw_toks, prefix):
    n = len(kw_toks)
    for i in range(len(tokens) - n + 1):
        ok = True
        for j, kt in enumerate(kw_toks):
            t = tokens[i + j]
            last = j == n - 1
            if last and prefix:
                if not t.startswith(kt):
                    ok = False
                    break
            elif t != kt:
                ok = False
                break
        if ok:
            return True
    return False


def classify(title: str, author: str = "", subjects=(), folders=()):
    """(ana_kategori, alt_kategori, puan) döndürür."""
    t_tokens = words(title + " " + " ".join(subjects))
    f_tokens = words(" ".join(folders))
    a_tokens = words(author.replace("-", " "))
    best = ("Diğer", "Sınıflandırılmamış", 0)
    for main, sub, kws, authors in _COMPILED:
        score = 0
        for toks, prefix in kws:
            if _match(t_tokens, toks, prefix):
                score += 3
            elif _match(f_tokens, toks, prefix):
                score += 2
        for a in authors:
            if _match(a_tokens, a, False) or _match(t_tokens, a, False):
                score += 3
        if score > best[2]:
            best = (main, sub, score)
    return best


def category_tree():
    tree = {}
    for main, sub, _ in CATEGORIES:
        tree.setdefault(main, [])
        if sub not in tree[main]:
            tree[main].append(sub)
    return tree


def classify_text(text, toc=()):
    """İçerik metnindeki anahtar kelime SIKLIĞINA göre puanlar.
    (ana, alt, puan, oran) döndürür; oran = en iyi puanın toplam içindeki payı."""
    from collections import Counter
    tokens = words(" ".join(toc) + " " + " ".join(toc) + " " + (text or ""))
    if len(tokens) < 40:
        return ("Diğer", "Sınıflandırılmamış", 0, 0.0)
    counts = Counter(tokens)
    vocab = list(counts)
    seq = tokens
    results = []
    for main, sub, kws, authors in _COMPILED:
        score = 0
        for toks, prefix in kws:
            if len(toks) == 1:
                kt = toks[0]
                if prefix:
                    n = sum(c for w, c in counts.items() if w.startswith(kt)) if len(kt) >= 4 else counts.get(kt, 0)
                else:
                    n = counts.get(kt, 0)
            else:
                n = sum(1 for i in range(len(seq) - len(toks) + 1) if _match(seq[i:i + len(toks)], toks, prefix))
            score += min(n, 8)
        for a in authors:
            if len(a) == 1:
                score += min(counts.get(a[0], 0), 4) * 2
            elif _match(seq, a, False):
                score += 4
        results.append((score, main, sub))
    total = sum(r[0] for r in results) or 1
    best = max(results)
    if best[0] < 4:
        return ("Diğer", "Sınıflandırılmamış", best[0], 0.0)
    return (best[1], best[2], best[0], round(best[0] / total, 2))

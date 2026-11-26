# Ukázkový rozhovor: „chci TV bez reklam“ → hotovo → „vrať to zpátky“

Texty bota jsou skutečné výstupy `TvPlaybook`, vygenerované s `FakeGuard`. Volání enginu je uvedené
kurzívou a zákazník ho nevidí. Každé „ano“ a „hotovo“ je reálná zpráva zákazníka. Bot předává enginu
jen její `message_id` a engine si text ověří přes chat vrstvu (`customer_message`).
Situace: Xiaomi Mi TV, zákazník nemá AdGuard Home, dočasný přístup k TV (ADB) zatím není.

---

**Zákazník:** Dobrý den, chci TV bez reklam.

*`detect_intents` → `["ads"]`, `plan(["ads"])` → `need_dns_choice` (AdGuard Home v síti není)*

**Bot:**
Reklamy v celé domácí síti nejlépe blokuje AdGuard Home ve vaší síti. Zatím ho nemáte — pokud chcete, domluví se s vámi správce Guardu na nastavení. Sám ho nastavovat nebudu.

Ve vaší síti nemáte AdGuard Home. Můžu místo toho na TV zapnout veřejný AdGuard DNS.
Upozornění: DNS dotazy půjdou přes AdGuard (třetí strana).
Když tahle služba nebude dostupná, TV se nepřipojí k internetu — před zapnutím proto ověřím, že funguje, a kdyby přestala, stačí napsat „vrať to zpátky“.
Chcete to? Odpovězte přesně „ano“ nebo „ne“.

**Zákazník:** ano

*`public_dns_answer(message_id)` → uloženo s id zprávy. `plan(["ads"])` → `need_user / enable_dev_mode`*

**Bot:**
1. Na TV otevřete Nastavení → Předvolby zařízení → Informace.
2. Najeďte na řádek „Sestavení“ a 7× rychle za sebou stiskněte OK na ovladači. Objeví se „Nyní jste vývojář“.
3. Vraťte se zpět do Předvoleb zařízení, otevřete „Pro vývojáře“ a zapněte „Ladění USB“.
4. Napište mi „hotovo“.

**Zákazník:** hotovo

*`user_enabled_dev_mode(message_id)` → doklad: `remote.*` téže TV je zapisovatelná → config flow `androidtv` na IP TV z kontextu*

**Bot:**
1. Na TV se teď objeví okno „Povolit ladění?“.
2. Potvrďte OK.
3. Volbu „Vždy povolit z tohoto počítače“ nezaškrtávejte — přístup je jen dočasný.
4. Napište mi „hotovo“.

**Zákazník:** hotovo

*`user_approved_adb(message_id, "ads")` → TV je připojená. Bot se ptá na aplikace.*

**Bot:** Díky, TV vidím. Které aplikace na TV používáte? Ty nechám úplně být.

**Zákazník:** Netflix a Voyo.

*`add_customer_apps(["Netflix", "Voyo"])`: Voyo = alias Oneplay (chrání `net.cme.voyo.cz*`). `plan(["ads"])` → snapshot, `check_dot` OK → `need_consent`*

**Bot:**
Televize: MiTV-MOOQ0 (192.0.2.14)

Co pro vás udělám:
→ Na TV bude méně reklam a TV bude o vás posílat méně údajů.

Konkrétně:
• Zapnu na TV veřejný AdGuard DNS (dotazy půjdou přes AdGuard, třetí stranu).
• Vypnu dočasný přístup k TV, který jsem potřeboval, a zkontroluji, že nic nezůstalo otevřené.

Tyhle aplikace nechám být: YouTube, Google Play, Služby Google Play, Google app, Asistent Google, Google TV, Domovská obrazovka Android TV, Chromecast, Oneplay, Netflix, HBO Max, Disney+, Prime Video, Kodi, SmartTube, Projectivy Launcher.
Používáte ještě jiné aplikace, které mám nechat? Napište je a přidám je mezi chráněné.

Ručně na TV (volitelné): Nastavení → Předvolby zařízení → Domovská obrazovka → vypnout „Povolit náhledy videí“ a „Povolit náhledy zvuku“.
Ručně na TV (volitelné): Nastavení → Předvolby zařízení → Informace → Právní informace → Reklamy → zapnout „Odhlásit personalizaci reklam“ (pokud TV nabízí).

Všechno půjde kdykoli vrátit — stačí napsat „vrať to zpátky“.
Můžu začít? Odpovězte přesně „ano“, nebo „ne“.

**Zákazník:** jo

*`consent(hash, message_id)` → `ambiguous`, nic se neprovede*

**Bot:** Promiňte, nejsem si jistý odpovědí. Napište prosím přesně „ano“, nebo „ne“.

**Zákazník:** ano

*`consent(hash, message_id)` → audit `consent` s `message_id` a časem zprávy → `check_dot` → DNS nastaveno → ladění vypnuto → porty 5555/9090 ověřené jako zavřené → `done`*

**Bot:**
Hotovo. Co jsem změnil:
• Zapnul jsem na TV veřejný AdGuard DNS.
• Dočasný přístup k TV jsem zase vypnul a ověřil, že je zavřený.
Kdyby cokoli nefungovalo jako dřív, napište „vrať to zpátky“ a všechno vrátím.
Pro jistotu můžete na TV v Nastavení → Předvolby zařízení → Pro vývojáře zvolit „Odvolat autorizace ladění USB“ a vypnout „Ladění USB“. Chcete poslat návod?

---

*O dva dny později:*

**Zákazník:** Nejde mi načíst jedna aplikace, vrať to zpátky.

*`detect_intents` → `["revert"]`, `plan_revert()` → `need_consent`*

**Bot:**
Televize: MiTV-MOOQ0 (192.0.2.14)

Vrátím vaši TV do stavu, ve kterém byla předtím:
• Vy na chvíli zapnete na TV ladění (návod pošlu).
• Vypnu na TV veřejný AdGuard DNS (vrátím původní DNS).
• Potom zase vypnu dočasný přístup k TV a ověřím to.

Můžu začít? Odpovězte přesně „ano“, nebo „ne“.

**Zákazník:** ano

**Bot:**
1. Abych mohl změny vrátit, potřebuji na chvíli znovu přístup k TV.
2. Na TV otevřete Nastavení → Předvolby zařízení → Pro vývojáře a zapněte „Ladění USB“.
3. Když se objeví okno „Povolit ladění?“, potvrďte OK (bez „Vždy povolit“).
4. Napište mi „hotovo“. Po dokončení přístup zase vypnu.

**Zákazník:** hotovo

**Bot:**
Hotovo, TV je zase ve stavu, ve kterém byla předtím.
Pro jistotu můžete na TV v Nastavení → Předvolby zařízení → Pro vývojáře zvolit „Odvolat autorizace ladění USB“ a vypnout „Ladění USB“. Chcete poslat návod?

---

## Varianty

- **Zákazník má AdGuard Home:** bez ADB. Bot zapne ochranu AdGuard Home (je-li vypnutá a zapisovatelná)
  a řekne, že na TV to platí, když router rozdává AdGuard Home jako DNS. Router sám nemění.
- **Chybí povolení k zápisu:** „K ovládání vaší TV zatím nemám povolení. Poslal jsem žádost správci
  Guardu ke schválení…“ + ruční postup.
- **Port 5555/9090 zůstal otevřený:** žádné „hotovo“. Eskalace adminovi a kroky „Odvolat autorizace
  ladění USB“ a vypnout Ladění USB. Po „hotovo“ se porty ověří znovu.
- **Vrácení selže:** změny zůstanou uložené. „Můžu to zkusit znovu (napište „zkusit znovu“), nebo to udělá
  technik ručně.“ Technik dostane skript přes admina, ne do chatu.
- **Víc televizí:** „Ve vaší síti vidím víc televizí. Kterou myslíte?“ Vybrat jde jen TV zákazníka.
- **Samsung / LG:** bot řekne, co jde a co ne, a nic nezkouší.
- **Zákazník pošle heslo do chatu:** bot ho nepoužije ani nezopakuje a doporučí ho změnit
  a zadat přímo v Home Assistantu.

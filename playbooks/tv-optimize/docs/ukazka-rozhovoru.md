# Ukázkový rozhovor: „chci TV bez reklam“ → hotovo → „vrať to zpátky“

Texty bota jsou skutečné výstupy `TvPlaybook` (ověřeno s `FakeGuard` v testech). Volání enginu
je uvedené kurzívou, zákazník ho nevidí. Zákazník: Xiaomi Mi TV, ještě bez dočasného přístupu (ADB).

---

**Zákazník:** Dobrý den, chci TV bez reklam.

*`detect_intents` → `["ads"]`, `plan(["ads"])` → `need_user / enable_dev_mode`*

**Bot:** Rád pomůžu — na TV pak bude méně reklam a TV bude o vás posílat méně údajů.
Abych to mohl nastavit, potřebuji od vás jednorázově zapnout na TV dočasný přístup:
1. Na TV otevřete Nastavení → Předvolby zařízení → Informace.
2. Najeďte na řádek „Sestavení“ a 7× rychle za sebou stiskněte OK na ovladači. Objeví se „Nyní jste vývojář“.
3. Vraťte se zpět do Předvoleb zařízení, otevřete „Pro vývojáře“ a zapněte „Ladění USB“.
4. Napište mi „hotovo“.

**Zákazník:** hotovo

*`user_enabled_dev_mode()` → config flow `androidtv` na IP TV zákazníka → `need_user / approve_adb`*

**Bot:**
1. Na TV se teď objeví okno „Povolit ladění?“.
2. Zaškrtněte „Vždy povolit z tohoto počítače“ a potvrďte OK.
3. Napište mi „hotovo“.

**Zákazník:** Hotovo!

*`user_approved_adb("ads")` → `info` (TV je připojená). Bot se ptá na aplikace.*

**Bot:** Díky, TV vidím. Které aplikace na TV používáte? Ty nechám úplně být.

**Zákazník:** Netflix, Oneplay a ještě Voyo.

*`add_customer_apps(["Netflix", "Oneplay", "Voyo"])`, `plan(["ads"])` → snapshot → `need_consent`*

**Bot:**
Co pro vás udělám:
→ Na TV bude méně reklam a TV bude o vás posílat méně údajů.

Konkrétně:
• Zapnu na TV filtr, který blokuje reklamy a sledovací služby (AdGuard DNS).
• Vypnu dočasný přístup k TV, který jsem potřeboval, a zkontroluji, že nic nezůstalo otevřené.

Tyhle aplikace nechám být: YouTube, Google Play, Služby Google Play, Google app, Asistent Google, Google TV, Domovská obrazovka Android TV, Chromecast, Oneplay, Netflix, HBO Max, Disney+, Prime Video, Kodi, SmartTube, Projectivy Launcher, Voyo.
Používáte ještě jiné aplikace, které mám nechat? Napište je a přidám je mezi chráněné.

Ručně na TV (volitelné): Nastavení → Předvolby zařízení → Domovská obrazovka → vypnout „Povolit náhledy videí“ a „Povolit náhledy zvuku“.
Ručně na TV (volitelné): Nastavení → Předvolby zařízení → Informace → Právní informace → Reklamy → zapnout „Odhlásit personalizaci reklam“ (pokud TV nabízí).

Všechno půjde kdykoli vrátit — stačí napsat „vrať to zpátky“.
Můžu začít? (ano / ne)

**Zákazník:** ano

*`consent(plan_hash)` → DNS nastaveno, ladění vypnuto, porty 5555/9090 zavřené → `done`*

**Bot:**
Hotovo. Co jsem změnil:
• Zapnul jsem na TV filtr reklam a sledování (AdGuard DNS).
• Dočasný přístup k TV jsem zase vypnul.
Kdyby cokoli nefungovalo jako dřív, napište „vrať to zpátky“ a všechno vrátím.

---

*O dva dny později:*

**Zákazník:** Nejde mi načíst jedna aplikace, vrať to zpátky.

*`detect_intents` → `["revert"]`, `plan_revert()` → `need_consent`*

**Bot:**
Vrátím vaši TV do stavu, ve kterém byla předtím:
• Vy na chvíli zapnete na TV ladění (návod pošlu).
• Vypnu filtr reklam na TV (vrátím původní DNS).
• Potom zase vypnu dočasný přístup k TV.

Můžu začít? (ano / ne)

**Zákazník:** jo

*`consent(hash)` → `waiting_user / reenable_adb`*

**Bot:**
1. Abych mohl změny vrátit, potřebuji na chvíli znovu přístup k TV.
2. Na TV otevřete Nastavení → Předvolby zařízení → Pro vývojáře a zapněte „Ladění USB“.
3. Když se objeví okno „Povolit ladění?“, potvrďte OK.
4. Napište mi „hotovo“. Po vrácení přístup zase vypnu.

**Zákazník:** hotovo

*`user_done()` → `settings delete …private_dns…`, ladění vypnuto → `done`*

**Bot:** Hotovo, TV je zase ve stavu, ve kterém byla předtím.

---

## Varianty

- **Chybí povolení k zápisu:** „K ovládání vaší TV zatím nemám povolení. Poslal jsem žádost správci
  Guardu ke schválení; jakmile ji schválí, ozvu se. Mezitím to můžete udělat ručně: …“
- **Ladění USB nejde zapnout (Mi TV):** bot vysvětlí Google účet / rodičovský zámek a pošle ruční postup.
- **Samsung / LG:** bot řekne, co u té TV jde (ovládání přes HA, AdGuard v síti přes správce) a co ne
  (Kodi, zrychlení systému), a nic nezkouší.
- **Zákazník pošle heslo do chatu:** bot ho nepoužije ani nezopakuje a doporučí ho změnit
  a zadat přímo v Home Assistantu.

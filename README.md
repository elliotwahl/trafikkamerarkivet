# Trafikkamerarkivet

Trafikverket har omkring 1 600 vägkameror utplacerade över Sverige. Bilderna
är öppna data och uppdateras ungefär en gång i minuten — men de sparas inte.
När en ny bild kommer är den gamla borta. Det finns inget arkiv, någonstans,
vilket gör det omöjligt att i efterhand se hur en väg såg ut under ett oväder,
en olycka eller en morgon då kön stod still.

Det här projektet bygger det arkivet, och lägger det öppet på Internet Archive.

## Källan

Bilderna kommer från Trafikverkets öppna API och kräver ingen nyckel:

```
https://api.trafikinfo.trafikverket.se/v2/Images/data/road.infrastructure.camera/TrafficFlowCamera_39636115.jpg?type=fullsize
```

Kameralistan hämtas från `api.trafikinfo.trafikverket.se/v2/data.json` med
`authenticationkey="demokey"`, nyckeln som står i Trafikverkets egen testbänk.
En egen nyckel är gratis och sätts med `TRV_NYCKEL` om demokey slutar fungera.

**Licens: Creative Commons Zero.** Fritt att arkivera och publicera vidare, utan
attributionskrav.

Uppmätt 2026-08-15:

| | antal aktiva | uppdateras | fullsize |
|---|---|---|---|
| Trafikflödeskameror | 785 | ~var 60:e sekund | 1280×720, ~125 KB |
| Väglagskameror | 737 | ~var 5:e minut | ~2 MP, ~340 KB |

Samma URL ger tre storlekar: `?type=fullsize` (1280×720), utan parameter
(385×217, ~13 KB) och `?type=thumbnail` (180×101, ~4 KB).

En detalj värd att känna till: sajter som visar de här kamerorna använder ofta
`?maxage=15` i sina URL:er. Det betyder bara "servera inte bilder äldre än
15 minuter" — inte att källan uppdateras så sällan. Mätt över 20 kameror ligger
medianen på 60 sekunder mellan nya bilder. Det finns alltså betydligt mer att
hämta än vad någon sajt visar.

## Så funkar det

```
var 15:e minut                              var 6:e timme
┌────────────────┐                     ┌──────────────────────┐
│ 1 API-anrop    │  PhotoTime för      │ periodens rutor       │
│                │  alla kameror       │ per kamera → AV1      │
└───────┬────────┘                     │ + register            │
        │ nya sedan sist?              └──────────┬───────────┘
┌───────▼────────┐                                │
│ GET fullsize   │  785 bilder                    ▼
│ 8 parallella   │  96 MB på 19 s        archive.org
└────────────────┘                        (ett item per dygn)
```

Ett enda API-anrop returnerar `PhotoTime` för samtliga kameror. Det är
dedup-nyckeln: vi vet exakt vilka kameror som har en ny bild innan vi laddat ner
en enda byte. Kameror som räknar upp tidsstämpeln men skickar identisk bild —
frusen bild, "no signal" — fångas av en sha256-jämförelse och sparas inte igen.

Ett fullt svep över alla 785 kameror tar 19 sekunder med 8 parallella
anslutningar, vilket är ungefär en förfrågan i sekunden i snitt. Ingen
rate limiting har observerats.

## Arkivets struktur

Ett item per dygn på archive.org, med alla kameror i:

```
trafikkamerarkivet-2026-08-15/
    SE_STA_CAMERA_0_1075001058-00.mp4    sex timmars rutor, en video
    SE_STA_CAMERA_0_1075001058-06.mp4
    ...
    index-00.json                         alla kameror, alla tidsstämplar
```

Strukturen är vald av en enda anledning: archive.org begränsar hur snabbt ett
konto får *skapa* items, men inte hur många filer man lägger i dem. Ett item per
kamera hade betytt 785 nya items på en eftermiddag, vilket deras spamskydd
stoppar direkt. Ett item per dygn betyder ett nytt item om dygnet.

URL:en till vilken kamera och vilket dygn som helst går att räkna ut utan
register:

```
https://archive.org/download/trafikkamerarkivet-2026-08-15/SE_STA_CAMERA_0_1075001058-06.mp4
```

`index-NN.json` mappar varje bildruta i videon till den exakta tidsstämpel
Trafikverket rapporterade, plus kamerans namn, väg, riktning och koordinater.

## Komprimering

En trafikkamera står still. Bakgrunden är identisk från ruta till ruta och bara
bilarna rör sig, vilket är precis vad en videokodek utnyttjar och vad en
stillbildskodek inte kan. Uppmätt på 16 rutor från Essingeleden, 1280×720:

| | KB/ruta | mot JPEG | PSNR | SSIM |
|---|---|---|---|---|
| JPEG, som Trafikverket levererar | 110 | — | — | — |
| AVIF q50, ruta för ruta | 60,0 | 1,8× | — | — |
| H.264 crf 26 | 75,8 | 1,4× | — | — |
| H.265 crf 38 | 13,2 | 8,2× | 38,5 | 0,954 |
| **AV1 crf 32** *(standard)* | **15,6** | **7,0×** | — | — |
| AV1 crf 40 | 10,4 | 10,4× | 38,0 | 0,952 |
| AV1 crf 45 | 7,8 | 13,9× | 37,0 | 0,944 |

Standardvalet är medvetet försiktigt: vid 200 % förstoring är enda synliga
skillnaden mot originalet att sensorbruset är borta.

Två saker som visade sig spela roll, och en som inte gjorde det:

- **Antalet rutor per video spelar stor roll.** Fyra rutor kostar 32 KB styck,
  sexton rutor 15,6 KB — nyckelbilden delas av fler.
- **Avståndet mellan rutorna spelar ingen roll.** 32,4 KB/ruta med en minut
  mellan rutorna, 32,0 KB med fem minuter. Bakgrunden är densamma oavsett.
- Därför komprimeras sextimmarsperioder, inte enskilda hämtningar.

## Köra själv

Kräver Python 3 och `ffmpeg`/`ffprobe`. Inga andra beroenden.

```sh
python3 src/collect.py              # ett svep
python3 src/compact.py              # packa gårdagens rutor
python3 src/compact.py --behall     # samma, utan att radera JPEG-rutorna
python3 src/upload.py --rensa       # ladda upp till archive.org, städa lokalt
python3 src/prune.py --kor          # rensa dygn äldre än RETENTION_DAGAR

BEGRANSA=12 python3 src/collect.py  # smoke test mot 12 kameror
```

För uppladdning behövs ett nyckelpar från
[archive.org/account/s3.php](https://archive.org/account/s3.php) i `.env`:

```
IA_ACCESS=...
IA_SECRET=...
```

## Inställningar

Allt styrs med miljövariabler.

| variabel | standard | |
|---|---|---|
| `ARKIV_DIR` | `data/arkiv` | var rutorna mellanlagras |
| `TYPER` | `Trafikflödeskamera` | lägg till `,Väglagskamera` för hela beståndet |
| `LAN` | alla | länsnummer, t.ex. `1` för Stockholm, `1,14` för Stockholm och Västra Götaland |
| `VARIANT` | `fullsize` | `fullsize` · `medium` · `thumbnail` |
| `PARALLELLA` | `8` | samtidiga nedladdningar |
| `MIN_LEDIGT_GB` | `5` | svepet avbryter hellre än fyller disken |
| `KODEK` / `CRF` | `av1` / `32` | `av1` · `h265` · `h264` |
| `RETENTION_DAGAR` | `0` | `0` = spara allt, annars rullande arkiv |
| `TRV_NYCKEL` | `demokey` | egen nyckel om demokey stryps |
| `KONTAKT` | – | mejladress i User-Agent |

## Två regler i koden

**Radera aldrig före verifiering.** Rutor tas bort först när archive.org självt
räknar upp filerna i sitt metadata-API. Går verifieringen inte att genomföra
ligger materialet kvar och nästa körning gör om försöket.

**Lita aldrig på att en video blev rätt.** Komprimeringen räknar rutorna i den
färdiga filen med `ffprobe` och vägrar radera originalen om antalet inte stämmer.

## Om personuppgifter

Trafikverket uppger att de tar bort personuppgifter ur de publicerade bilderna
"genom automatisk pixelering eller genom att bilderna publiceras med så låg
upplösning" att personer inte kan identifieras, och att bilderna därmed inte
längre innehåller personuppgifter. De reserverar sig för att enstaka undantag
kan förekomma. Hör av dig om du hittar en bild som identifierar någon, så
plockas den bort.

## Så körs det i drift

Ingenting lagras på en maskin som finns kvar efteråt. Svepet kör på en
efemär runner, bufferten ligger i objektlager, arkivet på archive.org.

```
var 15:e minut          var 6:e timme            en gång om dygnet
GitHub Actions          GitHub Actions           GitHub Actions
   svep.py       ──▶      pack.py         ──▶      status.py
      │                     │  ▲                      │
      ▼                     ▼  │ töms först när        ▼
  R2-buffert ───────────────┘  │ IA bekräftat      STATUS.md
  (tar per svep)               └── archive.org      (commit)
```

Allt tillstånd — vad som redan hämtats, när senaste svepet gick, hur mycket
som väntar på komprimering — lever i bufferten, inte på runnern.

### Vad som händer när något går sönder

| fel | hur det upptäcks |
|---|---|
| Ett svep hoppas över | Nästa svep tar den färskaste bilden. Inget larm — det kostar en bildruta. |
| Packningen slutar köra | Bufferten slutar tömmas, och svepet larmar när den passerar 6 GB av gratisnivåns 10. |
| En uppladdning misslyckas | Bufferten behålls, nästa packning gör om den. Perioder söks upp genom att de ligger kvar, inte genom att räknas ut — en missad körning tas igen av sig själv. |
| En video blev fel | `ffprobe` räknar rutorna i den färdiga filen. Stämmer inte antalet raderas inget. |
| Trafikverket svarar tomt | Tre försök. Ett tomt svar får aldrig tolkas som "det finns inga kameror". |
| **Hela insamlingen tystnar** | Ett jobb som inte kör kan inte larma om sig självt. Därför pingar svepet en extern dödmansknapp (`HJARTSLAG_URL`) — uteblir pingen larmar den tjänsten. |
| GitHub stänger av schemat | `status.py` committar en gång om dygnet, vilket håller repot aktivt. |

## Status

Se [STATUS.md](STATUS.md) för vad arkivet innehåller och när det senast
hämtade något.

## Licens

Koden: MIT. Bilderna: Creative Commons Zero, från Trafikverket.

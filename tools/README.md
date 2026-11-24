# შემოწმების ხელსაწყოები (`tools/`)

ეს საქაღალდე შეიცავს ორ განმეორებად ლოკალურ სკრიპტს ფრონტენდისა და საბეჭდი ფორმების შესამოწმებლად. არცერთი მათგანი არ გამოიყენება სერვერზე (production) და არ ემატება CI-ს.

> **უსაფრთხოება:** სკრიპტები იყენებენ მხოლოდ დროებით გამოგონილ (სინთეზურ) სატესტო მონაცემებს. გამომავალი საქაღალდეები (`tools/out/`, `tools/screenshots/`, `tools/pdfs/`) შეტანილია `.gitignore`-ში — არასოდეს დააკომიტოთ სქრინშოტები, PDF-ები ან რეალური პაციენტების მონაცემები.

---

## წინაპირობები (ლოკალურ გარემოში)

1. Python პაკეტები (`requirements/dev.txt`):
   ```bash
   pip install -r requirements/dev.txt
   python -m playwright install chromium
   ```
2. PDF-ების შესამოწმებლად (`tools/check_pdfs`) საჭიროა **LibreOffice** (`soffice` / `libreoffice`) და **Poppler** (`pdftoppm`):
   ```bash
   # Ubuntu / Debian:
   sudo apt-get install libreoffice-writer-nogui fonts-liberation fonts-noto-core poppler-utils
   ```

---

## 1. `tools/smoke` — მთავარი გვერდების ბრაუზერული შემოწმება

Playwright-ით (headless Chromium) გადის ავტორიზაციას სატესტო მომხმარებლით dev სერვერზე, ხსნის 9 მთავარ გვერდს:
- `/niv-300-9-a/cover/`
- `/niv-300-9-a/templates/`
- `/templates/`
- `/analysis/`
- `/blood/`
- `/consent/`
- `/board/`
- `/roster/`
- `/quotes/`

ამოწმებს თითოეულს ოთხ სიგანეზე (`390`, `768`, `1366`, `1920`), ბეჭდავს ანგარიშს:
- HTTP სტატუსზე (`200`),
- კონსოლის, JavaScript-ისა და ჩატვირთული ფაილების შეცდომებზე,
- ჰორიზონტალურ სქროლზე (გამონაკლისია მხოლოდ `/niv-300-9-a/cover/`, სადაც დანიშნულების ფართო ცხრილს სქროლი ეკუთვნის),

და ინახავს სრულ სქრინშოტებს `tools/out/smoke/` საქაღალდეში. ნებისმიერი პრობლემისას აბრუნებს `exit code 1`-ს.

### გაშვება

თუ dev სერვერი უკვე გაშვებულია `http://127.0.0.1:8000`-ზე, სკრიპტი პირდაპირ მას დაუკავშირდება; თუ არ არის გაშვებული — თავად ჩართავს დროებით `runserver`-ს თავისუფალ პორტზე:

```bash
python tools/smoke
```

ცვლილებებამდე და ცვლილებების შემდეგ სქრინშოტების პიქსელ-პიქსელ შედარება:

```bash
python tools/smoke --out tools/out/smoke/before
# ... ცვლილებების შემდეგ ...
python tools/smoke --out tools/out/smoke/after
python tools/smoke --compare-dirs tools/out/smoke/before tools/out/smoke/after
```

დამატებითი პარამეტრები:
- `--url http://127.0.0.1:8000` — კონკრეტული dev სერვერის მისამართი;
- `--user <username> --password <password>` — სასურველი სატესტო მომხმარებელი.

---

## 2. `tools/check_pdfs` — დანიშნულების PDF-ების პიქსელური შემოწმება

აგენერირებს ფიქსირებული სინთეზური დანიშნულებების ნაკრებს (მინიმალური, სტანდარტული და 16-ვე რიგით სრულად შევსებული) `/niv-300-9-a/cover/print/?t=<id>` მისამართით (LibreOffice-ის გავლით), გადაჰყავს ყველა PDF გვერდი PNG-ში და ადარებს ორ გაშვებას პიქსელ-პიქსელ. თუნდაც 1 განსხვავებული პიქსელის შემთხვევაში აბრუნებს `exit code 1`-ს. დროებით შექმნილი ჩანაწერები დასრულებისთანავე იშლება ბაზიდან.

### გაშვება

ორი დამოუკიდებელი გაშვების ერთმანეთთან შედარება:

```bash
python tools/check_pdfs
```

ცვლილებებამდე ეტალონის (baseline) შენახვა და ცვლილებების შემდეგ შედარება:

```bash
python tools/check_pdfs --save tools/out/pdfs/baseline
# ... ცვლილებების შემდეგ ...
python tools/check_pdfs --compare tools/out/pdfs/baseline
```

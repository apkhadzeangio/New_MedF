# MedicalForms — დანიშნულების ფურცელი NIV-300-9/ა

კლინიკის შიდა საიტი, სადაც ივსება და იბეჭდება ფორმა NIV-300-9/ა (საქართველოს
შრომის, ჯანმრთელობისა და სოციალური დაცვის მინისტრის 2009 წლის 19 მარტის N108/ნ
ბრძანება). საიტი მუშაობს PythonAnywhere-ის **უფასო** პაკეტზე:
`https://medf.pythonanywhere.com`. ზედმეტი სერვისი არ სჭირდება.

> **ბეჭდვის ფორმა უცვლელია.** დანიშნულება იბეჭდება `templates/MDFNew.docx`-დან
> (PDF-ად). ცვლილებების შემდეგ ანაბეჭდი შევადარეთ ძველს, ძველი ბაზის მიგრაციის
> შემდეგაც (SQLite-ზე და MySQL-ზე), და ის პიქსელ-პიქსელ იდენტურია.

## რა არის საიტზე

| მენიუ | რა არის |
|---|---|
| **ახალი დანიშნულება** | ფორმის წინა მხარე (ის, რაც იბეჭდება) |
| **დანიშნულებები** | ყველა დანიშნულება, ყველა ექიმის. აქ არის ძიება, ბეჭდვა, ასლი და „შაბლონად გადატანა“ |
| **შაბლონები** | მზა ნაკრებები (ანევრიზმა, ლერიში, ბარძაყ-მუხლქვეშა…): მედიკამენტები, დანიშნულებები და საათები |
| **ნოზოლოგიების ანალიზი** | რომელი პრეპარატები ენიშნებათ კონკრეტულ დიაგნოზს (ICD-10 კოდით ან სიტყვით: ანევრიზმა, ლერიში…) და რა სიხშირით (%), ჩვეული დოზა და საათები. მონიშნულიდან იქმნება შაბლონი |
| მომხმარებლის მენიუ | ჩემი ფოტო · მედიკამენტების ბაზა · ბაზის ასლის ჩამოტვირთვა · გამოსვლა |

**შესვლა:** ირჩევთ თქვენს სახელს ან ფოტოს და წერთ პაროლს. კომპიუტერი გიმახსოვრებთ
1 წლის განმავლობაში (ყოველ შესვლაზე ვადა თავიდან ითვლება), ამიტომ დანიშნულების
გვერდი ყოველთვის ღიაა.

**დანიშნულების გვერდზე:**
- „📋 შაბლონის ჩასმა“ ჩასვამს შაბლონის მედიკამენტებსა და საათებს. პაციენტის მონაცემები რჩება.
- „შაბლონად შენახვა“ მიმდინარე მედიკამენტებს შაბლონად ინახავს.
- **Ctrl+S** — შენახვა.
- **Shift+დაწკაპუნება** საათზე — იგივე საათი სამივე დღეში.
- წამლის ძებნა: **↑↓** და **Enter**.

უკანა მხარე წაიშალა, რადგან არ გამოიყენება. ბაზიდან წაიშალა მისი ცარიელი სვეტები:
სასიცოცხლო მაჩვენებლები, ექიმი/ექთანი, მე-17 სტრიქონი.

## PythonAnywhere-ზე განახლება

ყველა ბრძანება სრულდება **Bash console**-ში (Dashboard → Consoles → Bash).
`~/MedicalForms` არის ძველი კოდის საქაღალდე. ზუსტ გზას ნახავთ **Web** ტაბზე → *Source code*.

**1. სარეზერვო ასლი (აუცილებლად, ბაზის სტრუქტურა იცვლება):**

```bash
cp -r ~/MedicalForms ~/MedicalForms-backup-$(date +%F)
```

```bash
mysqldump -u medf -h medf.mysql.pythonanywhere-services.com --no-tablespaces 'medf$MDF' > ~/mdf-backup-$(date +%F).sql
```

**2. ახალი კოდი:**

```bash
cd ~ && git clone -b claude/dazzling-cerf-tp4313 https://github.com/apkhadzeangio/New_MedF.git MedicalForms-new
```

**3. `.env` ფაილი:**

```bash
cd ~/MedicalForms-new/src/MedicalForms && cp .env.example .env && python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

გახსენით `.env` (Files ტაბიდან) და შეავსეთ:
- `DJANGO_SECRET_KEY` — ზემოთ დაგენერირებული სტრიქონი;
- `DB_PASSWORD` — MySQL-ის პაროლი;
- `STAFF_USERS` — თანამშრომლების სია.

**4. ბიბლიოთეკები** (თუ საიტი virtualenv-ს იყენებს, ჯერ `workon <სახელი>`):

```bash
cd ~/MedicalForms-new && pip install -r requirements/prod.txt
```

**5. ბაზის განახლება, თანამშრომლები, სტატიკური ფაილები:**

```bash
cd ~/MedicalForms-new/src/MedicalForms && python manage.py showmigrations mdf
```

ბოლო ხაზი ასეთი უნდა იყოს: `[ ] 0014_remove_back_side_add_templates_staff`. ყველა
წინა უნდა იყოს მონიშნული `[X]`. თუ ასეა:

```bash
python manage.py migrate && python manage.py setup_staff && python manage.py collectstatic --noinput
```

**6. გადართვა:** Web ტაბზე *Source code* და *Working directory* შეცვალეთ
`~/MedicalForms-new/src/MedicalForms`-ზე, WSGI ფაილში `path` შეცვალეთ იმავეზე.
*Static files* ჩანაწერში `/static/` მიუთითეთ `.../src/MedicalForms/staticfiles`.
დააჭირეთ **Reload**.

**7. ყოველდღიური სარეზერვო ასლი (უფასოა):** Tasks ტაბზე დაამატეთ ყოველდღიური ამოცანა:

```bash
cd ~/MedicalForms-new/src/MedicalForms && python manage.py backup_data
```

ასლები ინახება `~/medf-backups`-ში. რჩება ბოლო 14. აღდგენა:
`python manage.py loaddata <ფაილი>.json.gz`.

**დაბრუნება ძველზე:** Web ტაბზე გზები დააბრუნეთ ძველ საქაღალდეზე, ბაზა აღადგინეთ
`mysql ... 'medf$MDF' < ~/mdf-backup-....sql`-ით და დააჭირეთ Reload.

## ლოკალურად გაშვება და ტესტები

```bash
cd src/MedicalForms && pip install -r ../../requirements/prod.txt
```

```bash
python manage.py migrate --settings=MedicalForms.settings.dev && python manage.py runserver --settings=MedicalForms.settings.dev
```

```bash
python manage.py test --settings=MedicalForms.settings.dev
```

PDF ბეჭდვისთვის საჭიროა LibreOffice (`soffice`).

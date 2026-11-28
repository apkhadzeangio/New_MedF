"""Plain-Georgian translation & jargon scrubbing for patient-facing and nurse-facing surfaces."""
import math
import re

_ASA_PLAIN_MAP = {
    'ASA I': 'მსუბუქი (1)',
    'ASA II': 'ზომიერი (2)',
    'ASA III': 'მძიმე (3)',
    'ASA IV': 'კრიტიკული (4)',
    'ASA V': 'უკიდურესად მძიმე (5)',
}

_CLINICIAN_JARGON_TOKENS = (
    'EHRA', 'PAUSE', 'CrCl', 'eGFR', 'ESUR', 'FDA', 'Assoc.', 'Multi-Society', 'ESAIC',
    'ESVS', 'ESC', 'ASRA', 'CHEST', 'D+1', 'LMWH', 'VKA', 'UFH', 'HIT', 'DKA', 'NPH', 'POISE-3',
    'HFpEF', 'HFrEF', 'ULN', 'EVLA', 'DOAC', 'SGLT2', 'GLP-1', 'VTE', 'CYP2C9', 'CYP2C19',
    'P-gp', 'aPTT', 'APTT', 'anti-Xa', 'AFib', 'INR', 'P2Y12', 'DAPT', 'PTA', 'Class III', 'Class I',
    'SBP', 'DBP', 'HR<', 'HR≥', 'K+≥', 'IV ', ' / CCB', '(PPI)', 'NSAID', 'პერიოპერაციულად',
    'ჰიდროკორტიზონ', 'ეტომიდატ', 'უანტიაგრეგანტოდ', 'სავალდებულოა ჩანაცვლება',
    '≥3x', '>=3x', 'დე-ესკალაცი', 'დეესკალაცი', 'მექ. სარქვ', 'Child-Pugh', 'Black Box', 'Boxed Warning',
)

_PHRASE_REPLACEMENTS = (
    # --- High-priority multi-word clinical phrases ---
    ('NSAID ალერგია!', 'ტკივილგამაყუჩებელზე ალერგია!'),
    ('NSAID ალერგია', 'ტკივილგამაყუჩებელზე ალერგია'),
    ('შეიყვანეთ კრეატინინი თარიღისთვის', 'ექიმი დაგიზუსტებთ შეჩერების ზუსტ დღეს თირკმლის ანალიზის მიხედვით — თვითნებურად არ შეწყვიტოთ'),
    ('შეიყვანეთ CrCl თარიღისთვის', 'ექიმი დაგიზუსტებთ შეჩერების ზუსტ დღეს თირკმლის ანალიზის მიხედვით — თვითნებურად არ შეწყვიტოთ'),
    ('შეიყვანეთ კრეატინინი რეჟიმისთვის', 'ექიმი დაგიზუსტებთ მიღების წესს თირკმლის ანალიზის მიხედვით — თვითნებურად არ შეწყვიტოთ'),
    ('ერთეული დასაზუსტებელია', 'ექიმი გადაამოწმებს თირკმლის ანალიზს და გეტყვით ზუსტ დღეს — თვითნებურად არ შეწყვიტოთ'),
    ('სავალდებულოა სტაციონარული IV ხიდი (ბივალირუდინი/არგატრობანი — HIT: LMWH/ჰეპარინი არა!)', 'სავალდებულოა კლინიკაში წინასწარ წოლა და ვენაში დამცავი წამალი ექიმის მეთვალყურეობით (თვითნებურად არ შეწყვიტოთ!)'),
    ('მაღალი თრომბორისკი + HIT: არაჰეპარინული ხიდი ჰემატოლოგთან (LMWH/ჰეპარინი არა!)', 'თრომბის მაღალი რისკის გამო საჭიროა ექიმის შერჩეული სპეციალური დამცავი წამალი (თვითნებურად არ შეწყვიტოთ!)'),
    ('სავალდებულოა სტაციონარული IV UFH ხიდი (CrCl<30/დიალიზი: თვითნებურად არ შეწყვიტოთ!)', 'სავალდებულოა კლინიკაში წინასწარ წოლა და ვენაში დამცავი გადასხმა ექიმის მეთვალყურეობით (თვითნებურად არ შეწყვიტოთ!)'),
    ('ორსულობა + მექანიკური სარქველი + HIT — ჰემატოლოგის, კარდიოლოგისა და მეანის კონსილიუმი (UFH/LMWH აკრძალულია)!', 'ორსულობის, გულის სარქვლისა და წამალზე ალერგიის გამო თვითნებურად არ შეწყვიტოთ — სასწრაფოდ დაუკავშირდით ექიმებს (ჰემატოლოგს, კარდიოლოგსა და მეანს)!'),
    ('ორსულობა + მექანიკური სარქველი — კონსილიუმი!', 'ორსულობისა და გულის სარქვლის გამო თვითნებურად არ შეწყვიტოთ — სასწრაფოდ შეათანხმეთ მეანთან და კარდიოლოგთან!'),
    ('ორსულობა + მექ. სარქველი — კონსილიუმი!', 'ორსულობისა და გულის სარქვლის გამო თვითნებურად არ შეწყვიტოთ — სასწრაფოდ შეათანხმეთ მეანთან და კარდიოლოგთან!'),
    (' [CYP2C9: ვარფარინთან აძლიერებს ანტიკოაგულაციას — გადაამოწმეთ INR!]', ' (ვარფარინთან ერთად მიღებისას აუცილებელია სისხლის შედედების ანალიზის გადამოწმება ექიმთან)'),
    ('[CYP2C9: ვარფარინთან აძლიერებს ანტიკოაგულაციას — გადაამოწმეთ INR!]', '(ვარფარინთან ერთად მიღებისას აუცილებელია სისხლის შედედების ანალიზის გადამოწმება ექიმთან)'),
    ('ღვიძლი ≥3x — შეათანხმეთ ექიმთან!', 'ღვიძლის ანალიზის გამო მიღება ან შეწყვეტა აუცილებლად შეათანხმეთ ექიმთან!'),
    ('≥3x-ზე', '3-ჯერ მეტზე'),
    ('>=3x-ზე', '3-ჯერ მეტზე'),
    ('≥3x', '3-ჯერ მეტი'),
    ('>=3x', '3-ჯერ მეტი'),
    (' [≥3 ანტითრომბოზული — შეათანხმეთ დე-ესკალაცია ქირურგთან!]', ' (რამდენიმე გამათხელებელი წამლის გამო სქემა აუცილებლად შეათანხმეთ ქირურგთან!)'),
    ('შეათანხმეთ დე-ესკალაცია ქირურგთან!', 'სქემა აუცილებლად შეათანხმეთ ქირურგთან!'),
    ('შეათანხმეთ დე-ესკალაცია!', 'სქემა აუცილებლად შეათანხმეთ ექიმთან!'),
    ('კლოპიდოგრელზე დეესკალაცია', 'კლოპიდოგრელზე გადასვლა'),
    ('დე-ესკალაცია', 'სქემის გამარტივება'),
    ('დეესკალაცია', 'სქემის გამარტივება'),
    # --- Guideline / society / trial citations ---
    ('(Multi-Society (ASA/AGA/ASMBS/ISPCOP/SAGES))', ''),
    ('Multi-Society (ASA/AGA/ASMBS/ISPCOP/SAGES)', ''),
    ('Multi-Society 2024', ''),
    ('Multi-Society', ''),
    ('Assoc. of Anaesthetists 2020', ''),
    ('Assoc. of Anaesthetists', ''),
    ('(FDA Black Box / EMA)', ''),
    ('FDA Black Box / EMA', ''),
    ('FDA Boxed Warning', 'მაღალი სისხლდენის რისკი'),
    ('(ESC 2018 / FDA Category D)', ''),
    ('ESC 2018 / FDA Category D', ''),
    ('ESC 2018', ''),
    ('FDA Category D', ''),
    ('(FDA / KDIGO)', ''),
    ('FDA / KDIGO', ''),
    ('KDIGO', ''),
    ('(ESUR v10 / FDA)', ''),
    ('ESUR v10 / FDA', ''),
    ('ESUR v10', ''),
    ('FDA Black Box', 'მაღალი რისკის გაფრთხილება'),
    ('Black Box', 'მაღალი რისკის გაფრთხილება'),
    ('Boxed Warning', 'მაღალი რისკის გაფრთხილება'),
    ('(ESVS 2024 / ACC-AHA Class III — Harm)', ''),
    ('ESVS 2024 / ACC-AHA Class III — Harm', ''),
    ('ACC/AHA 2024', ''),
    ('ACC-AHA 2024', ''),
    ('ACC/AHA', ''),
    ('ACC-AHA', ''),
    ('ESC / ESVS / EMA გაიდლაინით', 'საერთაშორისო წესებით'),
    ('ESC / ESVS / EMA', ''),
    ('ESC 2022 / FDA;', ''),
    ('ESC 2022 / FDA', ''),
    ('(CHEST 2022 / ESC 2022)', ''),
    ('(CHEST 2022)', ''),
    ('CHEST 2022', ''),
    ('(EHRA 2021 / PAUSE)', ''),
    ('(EHRA 2021)', ''),
    ('EHRA 2021 / PAUSE', ''),
    ('EHRA 2021', ''),
    ('EHRA', ''),
    ('PAUSE', ''),
    ('(ASRA 5th Ed)', ''),
    ('ASRA 5th Ed', ''),
    ('ASRA 2025', ''),
    ('ASRA/ESC', ''),
    ('ASRA-თი', 'უსაფრთხოების წესით'),
    ('ASRA', ''),
    ('ESVS 2024-ის', 'გაიდლაინის'),
    ('ESVS 2023-ის', 'გაიდლაინის'),
    ('ESVS 2023-ით', 'გაიდლაინით'),
    ('ESVS 2022-ით', 'გაიდლაინით'),
    ('ESVS 2024', ''),
    ('ESVS 2023', ''),
    ('ESVS 2022', ''),
    ('ESVS 2020', ''),
    ('ESVS Class I', ''),
    ('ESVS', ''),
    ('ESC 2022', ''),
    ('ESC Class III', 'მაღალი'),
    ('ESC Class I', ''),
    ('ESC', ''),
    ('ESAIC 2025', ''),
    ('ESAIC 2024', ''),
    ('ESAIC 2023', ''),
    ('ESAIC', ''),
    ('ESUR v10.0', ''),
    ('ESUR', ''),
    ('UK NVR', ''),
    ('NVR', ''),
    ('POISE-3', 'ექიმის სქემით'),
    ('STOP-or-NOT', ''),
    ('CPOC', ''),
    ('ERAS', ''),
    ('(ISTH 2021)', ''),
    ('ISTH 2021', ''),
    ('ISTH', ''),
    ('AAAAI 2022 გაიდლაინით', 'საერთაშორისო წესებით'),
    ('AAAAI 2022', ''),
    ('AAAAI', ''),
    ('IDSA 2019', ''),
    ('IDSA', ''),
    ('(ASA 2024)', ''),
    ('ASA 2024', ''),
    ('(ASA 2023)', ''),
    ('ASA 2023', ''),
    ('ASH 2018', ''),
    ('WHO Sign-In', 'ოპერაციის წინა შემოწმება'),
    ('WHO', ''),
    ('FDA / EMA', ''),
    ('EMA / FDA', ''),
    ('EMA', ''),
    ('FDA', ''),
    ('VOYAGER PAD', 'რივაროქსაბანის დაბალი დოზის სქემა'),
    ('VOYAGER', 'რივაროქსაბანის დაბალი დოზის სქემა'),
    ('Class III — Harm', 'აკრძალულია'),
    ('Class III (Harm)', 'აკრძალულია'),
    ('Class III', 'მაღალი რისკის'),
    ('Class IIa/C', ''),
    ('Class IIa', ''),
    ('Class IIb', ''),
    ('Class I/A', ''),
    ('Class I/B', ''),
    ('Class I', ''),
    ('Level A', ''),
    ('Level B', ''),
    ('Level C', ''),
    ('Child-Pugh B–C', 'ღვიძლის მძიმე უკმარისობა'),
    ('Child-Pugh B-C', 'ღვიძლის მძიმე უკმარისობა'),
    ('Child-Pugh', 'ღვიძლის უკმარისობა'),
    # --- Diagnostic / vascular / anatomical phrases ---
    ('კარდიოლოგიური Red Flag', 'გულის კრიტიკული სიგნალი'),
    ('Red Flag', 'კრიტიკული სიგნალი'),
    ('Hostile Neck', 'აორტის რთული ყელი'),
    ('Desert foot', 'ტერფის მძიმე სისხლძარღვოვანი დაზიანება'),
    ('Desert Foot', 'ტერფის მძიმე სისხლძარღვოვანი დაზიანება'),
    ('Iliac Conduit', 'თეძოს არტერიის დამხმარე მიდგომა'),
    ('Type & Screen', 'სისხლის ჯგუფი და შეთავსება'),
    ('Cell Saver', 'სისხლის დაზოგვის აპარატი'),
    ('Sign-In', 'ოპერაციის წინა შემოწმება'),
    ('Toe Pressure / TBI', 'თითის წნევის გაზომვა'),
    ('Toe Pressure', 'თითის წნევა'),
    ('Outflow (Profunda + SFA)', 'ბარძაყის გამავალი არტერიები'),
    ('ბარძაყის Outflow', 'ბარძაყის გამავალი არტერიები'),
    ('CFA / Profunda', 'ბარძაყის არტერიები'),
    ('Runoff კალაპოტი', 'ქვედა კიდურის გამავალი კალაპოტი'),
    ('BTK / Infrapopliteal', 'მუხლქვედა არტერიები'),
    ('near-occlusion vs', 'თითქმის სრული შევიწროება თუ'),
    ('near-occlusion', 'თითქმის სრული შევიწროება'),
    ('SVT / SFJ/SPJ', 'ზედაპირული ვენის თრომბი'),
    ('SFJ/SPJ', 'ღრმა ვენასთან შეერთების ადგილი'),
    ('ექო (LVEF) ან NT-proBNP', 'გულის ექოსკოპია ან გულის ანალიზი'),
    ('ფერიტინი / TSAT', 'რკინის მარაგის ანალიზი (ფერიტინი)'),
    ('PPI გასტროპროტექცია', 'კუჭის დამცავი წამალი'),
    ('DAPT / ანტითრომბოზული', 'ორმაგი გამათხელებელი სქემა'),
    ('VTE პროფილაქტიკა', 'თრომბის პრევენციის გეგმა'),
    ('CIED შემოწმება', 'კარდიოსტიმულატორის შემოწმება'),
    ('TSH / FT4', 'ფარისებრი ჯირკვლის ჰორმონები'),
    ('GSV კარტირება', 'საჩინო ვენის ულტრაბგერითი შემოწმება'),
    ('ABI / TP', 'ფეხის არტერიების წნევის გაზომვა'),
    ('კისრის CTA', 'კისრის კომპიუტერული ტომოგრაფია'),
    ('ტვინის CT/MRI', 'თავის ტვინის ტომოგრაფია'),
    ('CTA/MRI DICOM', 'კომპიუტერული ტომოგრაფიის დისკი'),
    ('CTA DICOM', 'კომპიუტერული ტომოგრაფიის დისკი'),
    ('DICOM', 'კვლევის დისკი'),
    ('EVAR ზომების დათვლა', 'აორტის სტენტის ზომების დათვლა'),
    ('CEA ანტიაგრეგანტი', 'საძილე არტერიის გამათხელებელი წამალი'),
    ('AAA დიამეტრი', 'აორტის ანევრიზმის დიამეტრი'),
    ('მარჯვ. ICA %', 'მარჯვენა საძილე არტერიის შევიწროება %'),
    ('მარცხ. ICA %', 'მარცხენა საძილე არტერიის შევიწროება %'),
    ('ICA-ს', 'საძილე არტერიის'),
    ('ICA', 'საძილე არტერია'),
    ('CT/MRI-ზე', 'ტომოგრაფიაზე'),
    ('CT/MRI', 'ტომოგრაფია'),
    ('CT-ით', 'ტომოგრაფიით'),
    ('CT-ზე', 'ტომოგრაფიაზე'),
    ('CTA-ზე', 'კომპიუტერულ ტომოგრაფიაზე'),
    ('CTA', 'კომპიუტერული ტომოგრაფია'),
    ('MRI', 'მაგნიტურ-რეზონანსული ტომოგრაფია'),
    ('>1/3 MCA', 'ტვინის დიდი ზონა'),
    ('MCA', 'ტვინის საშუალო არტერია'),
    ('DoS INR', 'ოპერაციის დილის შედედების ანალიზი'),
    ('DoS', 'ოპერაციის დილის'),
    ('0.9% NaCl-ით', 'ფიზიოლოგიური ხსნარით'),
    ('0.9% NaCl', 'ფიზიოლოგიური ხსნარი'),
    ('NaCl', 'ფიზიოლოგიური ხსნარი'),
    ('ΔNa+', 'ნატრიუმის ცვლილება'),
    ('β-hCG', 'ორსულობის ტესტი'),
    ('hCG', 'ორსულობის ტესტი'),
    ('β-OHB', 'სისხლის კეტონები'),
    ('BMT / SET', 'მედიკამენტური მკურნალობა და სიარულის ვარჯიში'),
    ('BMT/SET', 'მედიკამენტური მკურნალობა და სიარულის ვარჯიში'),
    ('BMT', 'მედიკამენტური მკურნალობა'),
    ('CAS/TCAR', 'საძილე არტერიის სტენტირება'),
    ('CAS / TCAR', 'საძილე არტერიის სტენტირება'),
    ('PBM კორექცია', 'სისხლის მაჩვენებლების კორექცია'),
    ('PBM', 'ანემიის კორექცია'),
    ('IV DTI', 'ვენაში არაჰეპარინული გამათხელებელი'),
    ('DTI', 'არაჰეპარინული გამათხელებელი'),
    ('IV სტრეს-დოზა', 'ვენაში დამცავი დოზა'),
    ('IV ხიდი', 'ვენაში დამცავი გადასხმა'),
    ('IV ', 'ვენაში '),
    ('SC ', 'კანქვეშ '),
    # --- Medications / labs / vitals ---
    ('არტერიული წნევა (BP)', 'არტერიული წნევა'),
    ('პულსი (HR)', 'პულსი'),
    ('(SBP<100 ან HR<55)', '(დაბალი წნევის ან ნელი პულსის გამო)'),
    ('(SBP<100 / K+≥5.2)', '(დაბალი წნევის ან მომატებული კალიუმის გამო)'),
    ('(თუ SBP≥100, HR≥55)', '(თუ წნევა და პულსი ნორმალურია)'),
    ('(SBP<110)', '(დაბალი წნევის გამო)'),
    ('(SBP≥110)', '(თუ წნევა ნორმალურია)'),
    ('(SBP≤DBP)', '(წნევის გადამოწმების შემდეგ)'),
    ('SBP ≤ DBP:', 'ზედა წნევა ქვედაზე ნაკლებია:'),
    ('SBP/DBP', 'წნევა'),
    ('(CYP2C19)', '(წამალთა თავსებადობისთვის)'),
    ('CYP2C19', 'წამალთა თავსებადობა'),
    ('CYP2C9', 'წამალთა თავსებადობა'),
    ('P-gp', 'წამალთა ურთიერთქმედება'),
    ('aPTT', 'შედედების ანალიზი'),
    ('APTT', 'შედედების ანალიზი'),
    ('anti-Xa', 'სისხლის შედედების ანალიზი'),
    ('ანტი-Xa', 'სისხლის შედედების'),
    ('AFib', 'მოციმციმე არითმია'),
    ('პერიოპერაციულად', 'ოპერაციის პერიოდში'),
    ('ენდოვასკულური PTA', 'სისხლძარღვის პროცედურა'),
    ('PTA-ზე', 'სისხლძარღვის პროცედურაზე'),
    ('(DAPT რეჟიმი სტენტირებისთვის)', '(ორმაგი დამცავი რეჟიმი სტენტირებისთვის)'),
    ('სავალდებულო LMWH ხიდით', 'სავალდებულო დროებითი დამცავი ნემსებით'),
    (' (ტექსტიდან)', ''),
    ('ACEi / ARB', 'წნევის წამალი (პრილი / სარტანი)'),
    ('ACEi/ARB', 'წნევის წამალი (პრილი / სარტანი)'),
    ('ამლოდიპინი / CCB', 'ამლოდიპინი (წნევის წამალი)'),
    (' / CCB', ''),
    (' (PPI)', ''),
    (' (DOAC)', ''),
    (' (SGLT2)', ''),
    (' (GLP-1)', ''),
    (' (VKA)', ''),
    (' / P2Y12', ''),
    (' (P2Y12)', ''),
    (' (CEA, მხარე B)', ' (ორივე მხარეს ერთდროულად)'),
    ('(მხარე B)', '(ორივე მხარე)'),
    ('(R ან L)', '(მარჯვენა ან მარცხენა)'),
    ('R ან L', 'მარჯვენა ან მარცხენა'),
    ('(R)', '(მარჯვენა)'),
    ('(L)', '(მარცხენა)'),
    ('(B)', '(ორივე მხარე)'),
    ('/ ICU /', '/ რეანიმაციის /'),
    ('ვიტ. K', 'ვიტამინი კ'),
    ('ვიტამინი K', 'ვიტამინი კ'),
    ('EMA Brilique SmPC 4.3 / FDA 5.5', ''),
    ('EMA SmPC 4.3', ''),
    ('EMA SmPC 4.6', ''),
    ('FDA Effient PI 5.1', ''),
    ('FDA PI 5.1', ''),
    ('RE-ALIGN / ESC Class III', 'მაღალი რისკის'),
    ('RE-ALIGN', ''),
    ('PROACT Xa', ''),
    ('ALI, Rutherford IIa/IIb', 'მწვავე იშემია'),
    ('Rutherford IIa/IIb', ''),
    ('Rutherford', ''),
    (' (CEA)', ''),
    (' (CTA)', ''),
    (' (AAA)', ''),
    (' (IC)', ''),
    (' (CLTI)', ''),
    (' (ALI)', ''),
    (' (DVT)', ''),
    (' (SVT)', ''),
    (' (HIT)', ''),
    (' (C2)', ' (მაღალი დონე)'),
    (' (IFU-ს გარეთ)', ''),
    ('IFU-ს გარეთ', 'სტანდარტული სტენტის ზომის გარეთ'),
    ('კონსილიუმის გარეშე', 'ექიმებთან შეთანხმების გარეშე'),
    ('კონსილიუმით', 'ექიმებთან შეთანხმებით'),
    ('კონსილიუმი!', 'ექიმებთან სასწრაფო შეთანხმება!'),
    ('კონსილიუმი', 'ექიმებთან შეთანხმება'),
    ('მექ. სარქველი', 'გულის მექანიკური სარქველი'),
    ('მექ. სარქველზე', 'გულის მექანიკურ სარქველზე'),
    ('მექ. სარქვლის', 'გულის მექანიკური სარქვლის'),
    ('LMWH ხიდი', 'დროებითი ნემსების სქემა'),
    ('ეუგლიკემიური DKA', 'სისხლში მჟავიანობის (კეტოაციდოზის)'),
    ('CrCl/eGFR', 'თირკმლის მაჩვენებლით'),
    ('eGFR / CrCl', 'თირკმლის მაჩვენებლის'),
    ('eGFR/CrCl', 'თირკმლის მაჩვენებლის'),
    ('HFrEF-ზე', 'გულის უკმარისობის გამო ექიმთან'),
    ('HFpEF', 'გულის სტაბილურ ფონზე'),
    ('HFrEF', 'გულის უკმარისობის გამო'),
    ('EVLA-ზე', 'ლაზერულ პროცედურაზე'),
    ('EVLA-ს', 'ლაზერული პროცედურის'),
    ('CEA-ზე', 'საძილე არტერიის ოპერაციაზე'),
    ('CEA-ს', 'საძილე არტერიის ოპერაციის'),
    ('AAA-ს', 'აორტის ანევრიზმის'),
    ('EVAR-ზე', 'აორტის სტენტირებაზე'),
    ('EVAR-ს', 'აორტის სტენტირების'),
    ('INR კონტროლი', 'სისხლის შედედების ანალიზი'),
    ('არასტრუქტურირებულია', 'დასაზუსტებელია'),
    ('არასტრუქტურირებული ჩანაწერი', 'დასაზუსტებელი წამალი'),
    ('x10^9/L', 'ათასი/მკლ'),
    ('µmol/L', 'მკმოლ/ლ'),
    ('umol/L', 'მკმოლ/ლ'),
    ('mg/dL', 'მგ/დლ'),
    ('pg/mL', 'პგ/მლ'),
    ('ng/L', 'ნგ/ლ'),
    ('L/min', 'ლ/წთ'),
    ('mmHg', 'მმ ვწყ.სვ.'),
    ('K+', 'კალიუმი'),
    ('Na+', 'ნატრიუმი'),
)

_TOKEN_MAP = {
    'NSAID': 'ტკივილგამაყუჩებელი',
    'Xa-DOAC': 'სისხლის გამათხელებელი',
    'DOAC': 'სისხლის გამათხელებელი',
    'VKA': 'ვარფარინი',
    'DAPT': 'ორმაგი გამათხელებელი სქემა',
    'SAPT': 'ერთი გამათხელებელი წამალი',
    'OAC': 'ანტიკოაგულანტი',
    'LMWH': 'დროებითი ნემსი',
    'UFH': 'ვენაში დამცავი წამალი',
    'P2Y12': 'თრომბის საწინააღმდეგო წამალი',
    'SGLT2': 'შაქრის წამალი',
    'GLP-1': 'წონის/შაქრის წამალი',
    'PPI': 'კუჭის დამცავი',
    'ACEi': 'წნევის წამალი',
    'ARB': 'წნევის წამალი',
    'CCB': 'წნევის წამალი',
    'NPH': 'საშუალო ხანგრძლივობის ინსულინი',
    'VRIII': 'ვენაში ინსულინის სქემა',
    'TXA': 'სისხლდენის შემჩერებელი',
    '4F-PCC': 'შედედების კონცენტრატი',
    'PCC': 'შედედების კონცენტრატი',
    'rt-PA': 'თრომბის დამშლელი',
    'IVT': 'თრომბოლიზისი',
    'HIT': 'ჰეპარინზე ალერგია',
    'CrCl': 'თირკმლის ფილტრაცია',
    'eGFR': 'თირკმლის ფილტრაცია',
    'INR': 'სისხლის შედედების ანალიზი',
    'TT': 'შედედების ანალიზი',
    'HbA1c': 'სამთვიანი შაქრის ანალიზი',
    'Hb': 'ჰემოგლობინი',
    'PLT': 'თრომბოციტები',
    'WBC': 'ლეიკოციტები',
    'TSAT': 'რკინის გაჯერება',
    'TSH': 'ფარისებრის ჰორმონი',
    'FT4': 'ფარისებრის ჰორმონი',
    'LDL': 'ქოლესტერინი',
    'NT-proBNP': 'გულის დატვირთვის ანალიზი',
    'hs-cTn': 'გულის კუნთის ანალიზი',
    'LVEF': 'გულის განდევნის ფრაქცია',
    'FEV1': 'სუნთქვის მაჩვენებელი',
    'SpO2': 'ჟანგბადის სატურაცია',
    'SBP': 'ზედა წნევა',
    'DBP': 'ქვედა წნევა',
    'HR': 'პულსი',
    'SI': 'შოკის ინდექსი',
    'BP': 'წნევა',
    'PP': 'პულსური წნევა',
    'BMI': 'სხეულის მასის ინდექსი',
    'ULN': 'ნორმის ზედა ზღვარი',
    'CO2': 'ნახშირორჟანგი',
    'ABI': 'ფეხის არტერიული ინდექსი',
    'TP': 'თითის წნევა',
    'TBI': 'თითის წნევის ინდექსი',
    'ICH': 'ტვინის სისხლჩაქცევა',
    'TIA': 'ტვინის სისხლის მიმოქცევის გარდამავალი დარღვევა',
    'CVA': 'ინსულტი',
    'MACE': 'გულის გართულების რისკი',
    'MINS': 'გულის დატვირთვის რისკი',
    'DVT': 'ღრმა ვენის თრომბოზი',
    'PE': 'ფილტვის ემბოლია',
    'SVT': 'ზედაპირული ვენის თრომბოზი',
    'VTE': 'თრომბის რისკი',
    'CKD': 'თირკმლის უკმარისობა',
    'CAD': 'გულის იშემიური დაავადება',
    'CHF': 'გულის უკმარისობა',
    'IHD': 'გულის იშემიური დაავადება',
    'DM': 'შაქრიანი დიაბეტი',
    'DKA': 'კეტოაციდოზი',
    'UTI': 'საშარდე ინფექცია',
    'ASB': 'უსიმპტომო ბაქტერიურია',
    'CIED': 'კარდიოსტიმულატორი',
    'ICD': 'დეფიბრილატორი',
    'PONV': 'ოპერაციის შემდგომი გულისრევა',
    'IPC': 'პნევმოკომპრესია',
    'RSI': 'სწრაფი ინტუბაცია',
    'ICU': 'რეანიმაცია',
    'HDU': 'ინტენსიური პალატა',
    'D+1': 'ოპერაციის მეორე დღე',
    'D-1': 'ოპერაციამდე 1 დღე',
    'D-3': 'ოპერაციამდე 3 დღე',
    'ACT': 'შედედების დრო',
    'GI': 'კუჭ-ნაწლავის',
    'AV': 'დიალიზის ფისტულის',
    'AAA': 'მუცლის აორტის ანევრიზმა',
    'EVAR': 'აორტის სტენტირება',
    'FEVAR': 'რთული აორტის სტენტირება',
    'CEA': 'საძილე არტერიის ოპერაცია',
    'CAS': 'საძილე არტერიის სტენტირება',
    'TCAR': 'საძილე არტერიის სტენტირება',
    'ABF': 'აორტის შუნტირება',
    'PTA': 'სისხლძარღვის ბალონირება/სტენტი',
    'EVLA': 'ლაზერული პროცედურა',
    'CLTI': 'მძიმე იშემია',
    'ALI': 'მწვავე იშემია',
    'PAD': 'არტერიების შევიწროება',
    'BTK': 'მუხლქვედა არტერიები',
    'GSV': 'დიდი საჩინო ვენა',
    'SSV': 'მცირე საჩინო ვენა',
    'AAGSV': 'დამატებითი საჩინო ვენა',
    'CFA': 'საერთო ბარძაყის არტერია',
    'SFA': 'ბარძაყის არტერია',
    'SMA': 'ნაწლავის მკვებავი არტერია',
    'IMA': 'ქვედა ნაწლავის არტერია',
    'IIA': 'თეძოს შიდა არტერია',
    'IFU': 'სტენტის ტექნიკური წესი',
    'IVUS': 'შიდასისხლძარღვოვანი ულტრაბგერა',
    'TTE': 'გულის ექოსკოპია',
    'ECG': 'ეკგ',
    'CXR': 'გულმკერდის რენტგენი',
    'SFJ': 'საზარდულის ვენური შეერთება',
    'SPJ': 'მუხლქვეშა ვენური შეერთება',
    'Outflow': 'გამავალი არტერია',
    'Runoff': 'გამავალი კალაპოტი',
    'Profunda': 'ბარძაყის ღრმა არტერია',
    'Infrapopliteal': 'მუხლქვედა არტერიები',
    'Shunt': 'დროებითი შუნტი',
    'RCRI': 'გულის რისკის ქულა',
    'VSG-CRI': 'გულის რისკის ქულა',
    'ARISCAT': 'ფილტვების რისკის ქულა',
    'GLASS': 'არტერიის სირთულის შეფასება',
    'WIfI': 'ტერფის იშემიის შეფასება',
    'CEAP': 'ვენების სტადია',
    'STOP-Bang': 'ძილის აპნოეს ქულა',
    'CFS': 'დაუძლურების ქულა',
    'NASCET': 'შევიწროების შეფასება',
    'mRS': 'ინვალიდობის ქულა',
    'ASA': 'ანესთეზიოლოგიური სიმძიმე',
    'NYHA': 'გულის უკმარისობის კლასი',
    'CARP': 'კარდიოლოგიური პროტოკოლი',
    'Caprini': 'თრომბის რისკის ქულა',
    'Mallampati': 'სასუნთქი გზის შეფასება',
    'Marfan': 'მარფანის სინდრომი',
    'Loeys-Dietz': 'შემაერთებელი ქსოვილის სინდრომი',
    'Crescendo': 'განმეორებითი მწვავე',
    'EHIT': 'აბლაციის შემდგომი თრომბი',
    'Gurm': 'კონტრასტის ზღვარი',
    'Rec': 'წესი',
    'Stage': 'სტადია',
    'Harm': 'რისკი',
    'mgdl': 'მგ/დლ',
    'umol': 'მკმოლ/ლ',
    'mL': 'მლ',
    'IC': 'სიარულის ტკივილი',
    'IV': 'ვენაში',
    'SC': 'კანქვეშ',
    'C6': 'ვენური წყლული',
    'C5': 'შეხორცებული წყლული',
    'C4': 'კანის ცვლილება',
    'C3': 'შეშუპება',
    'C2': 'ვარიკოზი',
    'IV-E': 'მე-4 გადაუდებელი',
    'III': 'მე-3',
    'II': 'მე-2',
}

_TOKEN_RE = re.compile(
    r'(?<![A-Za-z0-9])(?:'
    + '|'.join(re.escape(k) for k in sorted(_TOKEN_MAP, key=len, reverse=True))
    + r')(?![A-Za-z0-9])'
)

_2ND_PERSON_REPLACEMENTS = (
    ('არ შეწყვიტოს! მიიღოს', 'არ შეწყვიტოთ! მიიღეთ'),
    ('ბოლო აბი მიიღოს:', 'ბოლო აბი მიიღეთ:'),
    ('აღარ მიიღოს', 'აღარ მიიღოთ'),
)

_QUOTED_FT_RE = re.compile(r'თავისუფალ ტექსტში\s*\(„[^“”]{1,220}“\)')
_BAD_LATIN_VAL_RE = re.compile(r'=\s*[A-Za-z][A-Za-z0-9_.-]*')
_REG_CITATION_RE = re.compile(
    r'\b(?:EMA\s+(?:Brilique\s+)?(?:EPAR\s+)?SmPC|FDA\s+(?:Effient\s+|Brilinta\s+)?(?:PI\s+|Category\s+[A-DX]\b)?)(?:\s*\d+(?:\.\d+)*)?\b'
)
_DIGIT_UNIT_RE = re.compile(r'(\d)\s*(mg|mcg|ml|iu)\b', re.IGNORECASE)
_DIGIT_UNIT_KA = {'mg': 'მგ', 'mcg': 'მკგ', 'ml': 'მლ', 'iu': 'სე'}
_ORPHAN_SEC_PAREN_RE = re.compile(r'\(\s*\d+\.\d+(?:\.\d+)*\s*\)')
_RESIDUAL_LATIN_RE = re.compile(r'(?<![A-Za-z0-9])(?!STOP\b)(?!x2\b)(?!2x\b)(?!3x\b)[A-Za-zβµ][A-Za-z0-9_+./-]{1,}(?![A-Za-z0-9])')


_NONFINITE_KA_STRS = frozenset({'nan', '+nan', '-nan', 'inf', '+inf', '-inf', 'infinity', '+infinity', '-infinity'})


def _safe_ka_str(val):
    """Safely convert scalar text to str without huge-int ValueError, non-finite float/string, or container repr leakage."""
    if val is None or isinstance(val, (dict, list, tuple, set, bytes)):
        return ''
    if isinstance(val, float) and not math.isfinite(val):
        return ''
    if isinstance(val, int) and not isinstance(val, bool) and abs(val) > 10**12:
        return ''
    s = str(val)
    if s.strip().lower() in _NONFINITE_KA_STRS:
        return ''
    return s


def plain_asa_ka(asa_raw):
    """Translate 'ASA I..IV(-E)' into plain Georgian severity label without Latin abbreviations."""
    s = _safe_ka_str(asa_raw).strip()
    is_emerg = s.endswith('-E')
    base = s[:-2].strip() if is_emerg else s
    ka = _ASA_PLAIN_MAP.get(base, 'შეფასების პროცესში')
    return f'{ka} — გადაუდებელი' if is_emerg else ka


_INV_BP_ARM_R_RE = re.compile(r'(?<![A-Za-z0-9])R\s*(?=(?:\d+|\?)/(?:\d+|\?))')
_INV_BP_ARM_L_RE = re.compile(r'(?<![A-Za-z0-9])L\s*(?=(?:\d+|\?)/(?:\d+|\?))')
_ARM_LIMB_R_RE = re.compile(r'(?<![A-Za-z0-9])R (?=კიდურ)')
_ARM_LIMB_L_RE = re.compile(r'(?<![A-Za-z0-9])L (?=კიდურ)')


_EMPTY_PAREN_RE = re.compile(r'\([/·;,:—–\s-]*\)')
_EMPTY_BRACK_RE = re.compile(r'\[[/·;,:—–!\s-]*\]')
_LEAD_PAREN_RE = re.compile(r'\((?:[/·;,:—–\s-]+(?:\d+\.\d+(?:\.\d+)*(?:[/·;,:—–\s]|-(?![ა-ჰ]))*)?|\d+\.\d+(?:\.\d+)*(?:[/·;,:—–\s]|-(?![ა-ჰ]))+)')
_TRAIL_PAREN_RE = re.compile(r'(?<![/·;,:—–\s-])[/·;,:—–\s-]+\)')
_LEAD_BRACK_RE = re.compile(r'\[[/·;,:—–\s-]+')
_TRAIL_BRACK_RE = re.compile(r'(?<![/·;,:—–\s-])[/·;,:—–\s-]+\]')
_COLON_COMMA_RE = re.compile(r':\s*,+\s*')
_DASH_COLON_RE = re.compile(r'([—–])\s*:(?=\s|$)')
_DASH_SLASH_RE = re.compile(r'([—–])\s*/(?:\s*/)*\s*')
_DASH_PUNCT_RE = re.compile(r'(?<![\s—–/-])\s*[—–-][\s—–/-]*([!?.])')
_SPACE_PUNCT_RE = re.compile(r'(?<!\s)\s+([.,;:!]|\?(?!/))')
_MULTI_SPACE_RE = re.compile(r'\s{2,}')


def scrub_plain_ka(txt, second_person=True, strip_quoted_ft=False):
    """Scrub clinical abbreviations, guideline names, and Latin tokens into clean Georgian."""
    out = _safe_ka_str(txt)
    if not out:
        return ''
    if strip_quoted_ft:
        out = _QUOTED_FT_RE.sub('თავისუფალ ტექსტში', out)
        out = _BAD_LATIN_VAL_RE.sub(' (არასწორი ჩანაწერი)', out)
    out = _REG_CITATION_RE.sub('', out)
    out = _DIGIT_UNIT_RE.sub(lambda m: f'{m.group(1)} {_DIGIT_UNIT_KA[m.group(2).lower()]}', out)
    for old, new in _PHRASE_REPLACEMENTS:
        if old in out:
            out = out.replace(old, new)
    out = _INV_BP_ARM_R_RE.sub('მარჯვენა ', out)
    out = _INV_BP_ARM_L_RE.sub('მარცხენა ', out)
    out = _ARM_LIMB_R_RE.sub('მარჯვენა ', out)
    out = _ARM_LIMB_L_RE.sub('მარცხენა ', out)
    out = _TOKEN_RE.sub(lambda m: _TOKEN_MAP[m.group(0)], out)
    if second_person:
        for old, new in _2ND_PERSON_REPLACEMENTS:
            out = out.replace(old, new)
    if 'β-' in out or 'β ' in out:
        out = out.replace('β-', 'ბეტა-').replace('β ', 'ბეტა ')
    # Strip any residual non-Georgian technical token (e.g. raw English drug brand or code)
    if strip_quoted_ft:
        out = _RESIDUAL_LATIN_RE.sub('', out)
    out = _ORPHAN_SEC_PAREN_RE.sub('', out)
    # Strictly O(N) linear-time punctuation cleanup (zero nested quantifiers, anchored via lookbehinds)
    out = _EMPTY_PAREN_RE.sub('', out)
    out = _EMPTY_BRACK_RE.sub('', out)
    out = _LEAD_PAREN_RE.sub('(', out)
    out = _TRAIL_PAREN_RE.sub(')', out)
    out = _LEAD_BRACK_RE.sub('[', out)
    out = _TRAIL_BRACK_RE.sub(']', out)
    out = _EMPTY_PAREN_RE.sub('', out)
    out = _EMPTY_BRACK_RE.sub('', out)
    out = _COLON_COMMA_RE.sub(': ', out)
    out = _DASH_COLON_RE.sub(r'\1', out)
    out = _DASH_SLASH_RE.sub(r'\1 ', out)
    out = _DASH_PUNCT_RE.sub(r'\1', out)
    out = _SPACE_PUNCT_RE.sub(r'\1', out)
    out = _MULTI_SPACE_RE.sub(' ', out).strip()
    return out


_DRUG_PLAIN_PREFIX = {
    'SGLT2 (ჯარდიანსი / ფორსიგა)': 'შაქრის წამალი — ჯარდიანსი / ფორსიგა',
    'DOAC (ქსარელტო / ელიქვისი)': 'სისხლის გამათხელებელი — ქსარელტო / ელიქვისი',
    'ქსარელტო 2.5 მგ x2': 'სისხლის გამათხელებელი — რივაროქსაბანი (დაბალი დოზა)',
    'დაბიგატრანი (პრადაქსა)': 'სისხლის გამათხელებელი — პრადაქსა (დაბიგატრანი)',
    'ვარფარინი': 'სისხლის გამათხელებელი — ვარფარინი',
    'კლოპიდოგრელი / P2Y12': 'თრომბის საწინააღმდეგო — კლოპიდოგრელი (პლავიქსი)',
    'ასპირინი (75–100 მგ)': 'ასპირინი (კარდიომაგნილი / თრომბო-ასი)',
    'GLP-1 (ოზემპიკი / მუნჯარო / საქსენდა)': 'წონის/შაქრის ნემსი — ოზემპიკი / მუნჯარო',
    'ACEi / ARB (პრილი/სარტანი)': 'წნევის წამალი (პრილი / სარტანი)',
}


def plain_drug_name(raw_drug):
    safe = _safe_ka_str(raw_drug)
    return scrub_plain_ka(_DRUG_PLAIN_PREFIX.get(safe, safe), second_person=False, strip_quoted_ft=True)


_SF_UNIT_ONLY_RE = re.compile(
    r'(?<![ა-ჰ])(?:მგ|მკგ|მლ|სე|გ|ტაბ|ტაბლეტი|აბი|აბ|კაფსულა|დოზა|დღეში|დღ|კვ|სთ|წონის|ჯერ'
    r'|ვიღებ|ვსვამ|იღებს|სვამს|ვიკეთებ|იკეთებს|ვაგრძელებ|აგრძელებს|მაქვს|აქვს|მქონდა|ჰქონდა|აღენიშნება'
    r'|ალერგია|ალერგიული|ალერგიას|ანაფილაქსია|ანაფილაქსიური|შოკი|გამონაყარი|შეშუპება|ქავილი|რეაქცია'
    r'|წამალი|წამალზე|წამლებზე|პრეპარატი|პრეპარატზე|ზე|და|ან|დილას|საღამოს|ღამით|ყოველდღე|მხოლოდ|მძიმე|მსუბუქი)(?![ა-ჰ])'
)
_SF_KA_WORD_RE = re.compile(r'[ა-ჰ]{2,}')
_SF_LONE_LATIN_RE = re.compile(r'(?<![A-Za-z0-9])[A-Za-z](?![A-Za-z0-9])')
_SF_LATIN_WORD_RE = re.compile(r'[a-z]{2,}')
_SF_MED_CLAUSE_SPLIT_RE = re.compile(r'[,;+/\n]+| (?:და|ან|and|or) ')
_SF_LATIN_FILLER = frozenset({
    'mg', 'mcg', 'ml', 'iu', 'tab', 'tabs', 'tablet', 'tablets', 'cap', 'caps', 'bid', 'tid', 'qid', 'qd', 'od',
    'once', 'twice', 'daily', 'morning', 'evening', 'night', 'po', 'iv', 'sc', 'im', 'sq',
    'and', 'or', 'with', 'without', 'for', 'on', 'in', 'at', 'to', 'of', 'the', 'per', 'by',
    'taking', 'takes', 'using', 'uses', 'continuing', 'continues', 'started', 'stopped', 'held',
    'allergy', 'allergic', 'allergies', 'anaphylaxis', 'rash', 'reaction', 'drug', 'med', 'meds', 'medication', 'medicine', 'dose',
    'day', 'days', 'week', 'weeks', 'month', 'months', 'stop', 'yes', 'no', 'not', 'ok', 'vit', 'vitamin',
})
_SF_COMMON_MED_LABELS = (
    (('ბისოპროლ', 'bisoprol', 'კონკორ', 'concor', 'მეტოპროლ', 'metoprol', 'ნებივოლ', 'nebivol', 'კარვედილ', 'carvedil'), 'ბეტა-ბლოკერი (გულის/წნევის წამალი)'),
    (('ამლოდიპ', 'amlodip', 'ლერკანიდიპ', 'lercanidip', 'ნიფედიპ', 'nifedip'), 'წნევის წამალი (კალციუმის ბლოკერი)'),
    (('ფუროსემ', 'furosem', 'ტორასემ', 'torasem', 'ინდაპამ', 'indapam', 'სპირონოლაქტ', 'spironolact', 'ჰიდროქლოროთიაზ', 'hydrochlorothiaz'), 'შარდმდენი'),
    (('თიროქს', 'thyrox', 'ეუთიროქს', 'euthyrox', 'ლევოთიროქს', 'levothyrox'), 'ფარისებრი ჯირკვლის წამალი'),
    (('პარაცეტამ', 'paracetam', 'acetaminophen', 'პანადოლ', 'panadol', 'ანალგინ', 'analgin', 'სპაზმალგ', 'spasmalg'), 'ტკივილგამაყუჩებელი / სიცხის დამწევი'),
    (('ამოქსიც', 'amoxic', 'ოგმენტინ', 'augmentin', 'ცეფალექს', 'cephalex', 'cefalex', 'ცეფუროქს', 'cefurox', 'აზითრომ', 'azithrom', 'ციპროფლოქს', 'ciproflox'), 'ანტიბიოტიკი'),
    (('ვიტამინი დ', 'vitamin d', 'vit d', 'ქოლეკალციფერ', 'cholecalcifer', 'დ ვიტამინ'), 'ვიტამინი დ'),
    (('ვიტამინ', 'vitamin'), 'ვიტამინი'),
)


def _has_meaningful_ka_text(txt):
    return bool(_SF_KA_WORD_RE.search(_SF_UNIT_ONLY_RE.sub(' ', txt or '')))


def _has_unrecognized_latin_word(pos_low, known_stems):
    for w in _SF_LATIN_WORD_RE.findall(pos_low or ''):
        if w in _SF_LATIN_FILLER:
            continue
        if not any((st in w or w in st) for st in known_stems):
            return True
    return False


def _has_unrecognized_ka_word(pos_low, ka_stems):
    cleaned = _SF_UNIT_ONLY_RE.sub(' ', pos_low or '')
    for w in _SF_KA_WORD_RE.findall(cleaned):
        if not any(st in w for st in ka_stems):
            return True
    return False


def _has_unrecognized_med_clause(pos_low, all_stems):
    ascii_stems = [st for st in all_stems if st.isascii()]
    if _has_unrecognized_latin_word(pos_low, ascii_stems):
        return True
    ka_stems = [tok for st in all_stems if not st.isascii() for tok in st.replace('-', ' ').split() if len(tok) >= 2]
    if _has_unrecognized_ka_word(pos_low, ka_stems):
        return True
    for part in _SF_MED_CLAUSE_SPLIT_RE.split(pos_low or ''):
        p = part.strip()
        if not p or any(st in p for st in all_stems):
            continue
        if _has_meaningful_ka_text(p):
            return True
    return False


def format_sf_freetext_med_and_allergy(q, items):
    """Build plain-Georgian self-fill highlights for free-text meds/allergies without silent Latin or KA drug erasure."""
    from .constants import _FREETEXT_ALLERGY_STEMS, _FREETEXT_HIGH_RISK_MED_RULES
    from .meds import _detect_freetext_high_risk_meds
    from .meds_freetext import (
        _ASPIRIN_FT_STEMS,
        _FT_ACEI_ARB_STEMS,
        _FT_AMIODARONE_STEMS,
        _FT_ARGATROBAN_STEMS,
        _FT_FONDAPARINUX_STEMS,
        _FT_HEPARIN_LMWH_STEMS,
        _FT_NSAID_STEMS,
        _FT_STATIN_STEMS,
        _detect_freetext_allergies,
        _has_unrecognized_allergy_clause,
        _positive_clauses_text,
    )
    from .utils import _safe_display_str, _str_list, chip_summary

    raw_other_meds = q.get('other_meds') if isinstance(q.get('other_meds'), str) else ''
    if raw_other_meds.strip():
        ft = _detect_freetext_high_risk_meds(raw_other_meds, q=q)
        lvl = 'urgent' if ft.get('unstructured_labels') else 'info'
        pos_low = _positive_clauses_text(raw_other_meds)
        all_med_stems = [
            st for _, _, _, sts in _FREETEXT_HIGH_RISK_MED_RULES for st in sts
        ] + [
            st for grp in (_ASPIRIN_FT_STEMS, _FT_STATIN_STEMS, _FT_ACEI_ARB_STEMS, _FT_AMIODARONE_STEMS, _FT_HEPARIN_LMWH_STEMS, _FT_FONDAPARINUX_STEMS, _FT_ARGATROBAN_STEMS, _FT_NSAID_STEMS) for st in grp
        ] + [st for sts, _ in _SF_COMMON_MED_LABELS for st in sts] + ['asa', 'lmwh', 'ufh', 'ლმვჰ', 'უფჰ']
        extra_unrec_med = _has_unrecognized_med_clause(pos_low, all_med_stems)
        ft_lbls = [scrub_plain_ka(lbl, second_person=False, strip_quoted_ft=True) for lbl in (ft.get('unstructured_labels') or [])]
        rule_lbls = {k: lbl for k, lbl, _, _ in _FREETEXT_HIGH_RISK_MED_RULES}
        for k in (ft.get('matched_keys') or ()):
            if k in rule_lbls and k not in (ft.get('unstructured_keys') or ()):
                ft_lbls.append(scrub_plain_ka(rule_lbls[k], second_person=False, strip_quoted_ft=True))
        for flag_k, ka_lbl in (('has_ft_statin', 'სტატინი'), ('has_ft_acei_arb', 'წნევის წამალი'), ('has_ft_nsaid', 'ტკივილგამაყუჩებელი'), ('has_omeprazole', 'კუჭის დამცავი')):
            if ft.get(flag_k):
                ft_lbls.append(ka_lbl)
        for stems, ka_lbl in _SF_COMMON_MED_LABELS:
            if any(st in pos_low for st in stems):
                if ka_lbl == 'ვიტამინი' and 'ვიტამინი დ' in ft_lbls:
                    continue
                ft_lbls.append(ka_lbl)
        ft_lbls = [x for x in dict.fromkeys(ft_lbls) if x]
        if ft_lbls:
            if extra_unrec_med:
                ft_lbls.append('დამატებითი წამალი ტექსტში (გადაამოწმეთ ჩანაწერი)')
            clean_other = ', '.join(ft_lbls)
        else:
            cand = _SF_LONE_LATIN_RE.sub('', scrub_plain_ka(_safe_display_str(raw_other_meds, max_len=160), second_person=False, strip_quoted_ft=True)).strip(' ,;:-')
            if _has_meaningful_ka_text(cand):
                clean_other = f'{cand} · დამატებითი წამალი ტექსტში (გადაამოწმეთ ჩანაწერი)' if _has_unrecognized_latin_word(pos_low, [st for st in all_med_stems if st.isascii()]) else cand
            else:
                clean_other = 'მითითებულია ტექსტში (გადაამოწმეთ ჩანაწერი)'
        items.append((lvl, f'სხვა წამალი: {clean_other}'))
    raw_alg_txt = q.get('allergy_text') if isinstance(q.get('allergy_text'), str) else ''
    pos_alg_txt = _positive_clauses_text(raw_alg_txt)
    ft_algs = _detect_freetext_allergies(raw_alg_txt)
    if q.get('allergy_iodine') or ('iodine' in ft_algs):
        items.append(('urgent', 'ალერგია იოდზე / კონტრასტზე!'))
    alg_ka_stems = [tok for _, sts in _FREETEXT_ALLERGY_STEMS for st in sts if not st.isascii() for tok in st.replace('-', ' ').split() if len(tok) >= 2] + ['იოდ', 'ლმვჰ']
    alg_en_stems = [st for _, sts in _FREETEXT_ALLERGY_STEMS for st in sts if st.isascii()] + ['iodin', 'hit', 'lmwh']
    extra_alg_unrec = bool(_has_unrecognized_allergy_clause(raw_alg_txt) or _has_unrecognized_latin_word(pos_alg_txt, alg_en_stems) or _has_unrecognized_ka_word(pos_alg_txt, alg_ka_stems))
    alg_chips_eff = list(dict.fromkeys([*_str_list(q.get('allergy_chips')), *(ft_algs - {'iodine'})]))
    if q.get('allergy_other') or alg_chips_eff or (pos_alg_txt and (extra_alg_unrec or 'iodine' not in ft_algs)):
        alg_txt = chip_summary(alg_chips_eff, 'allergy_chips', raw_alg_txt if (pos_alg_txt and not alg_chips_eff) else '') or 'მედიკამენტური ალერგია'
        clean_alg = _SF_LONE_LATIN_RE.sub('', scrub_plain_ka(alg_txt, second_person=False, strip_quoted_ft=True)).strip(' ,;:-')
        if not _has_meaningful_ka_text(clean_alg):
            clean_alg = 'მედიკამენტური ალერგია (გადაამოწმეთ ჩანაწერი)'
        elif extra_alg_unrec and (alg_chips_eff or _has_unrecognized_latin_word(pos_alg_txt, alg_en_stems)):
            clean_alg = f'{clean_alg} · დამატებითი ალერგენი ტექსტში (გადაამოწმეთ ჩანაწერი)'
        items.append(('urgent', f'ალერგია: {clean_alg}'))


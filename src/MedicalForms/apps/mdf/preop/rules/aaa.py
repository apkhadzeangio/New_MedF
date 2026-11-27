"""Infrarenal AAA pathway clinical rules (ESVS 2024 / ESC 2022)."""
from ..registry import rule_value


def _eval_aaa_anatomy(ctx, aaa_morph, aaa_neck, pop_scr):
    patient, q, s, aaa_mm = ctx.patient, ctx.q, ctx.s, ctx.aaa_mm
    giant_mm = rule_value('AAA_GIANT_MM')
    young_age = rule_value('AAA_YOUNG_AGE_GENETIC')

    if q.get('aaa_pain'):
        ctx.red_flags.append(
            'საეჭვოა სიმპტომური AAA (ახალი ტკივილი მუცლის/წელის/გვერდის არეში) — '
            'გეგმური კვლევების ნაცვლად დაუყოვნებლივ აცნობეთ ქირურგს!'
        )
    if aaa_morph == 'inflammatory_mycotic':
        ctx.red_flags.append(
            'საეჭვოა მიკოზური / ინფიცირებული ანევრიზმა (ESVS 2024) — '
            'სასწრაფოდ აცნობეთ ქირურგს (სისხლის ნათესები, ანტიბიოტიკოთერაპია და დაჩქარებული ტაქტიკა)!'
        )
    elif aaa_morph == 'inflammatory':
        ctx.amber_flags.append(
            'სტერილური ანთებითი AAA / პერიაორტიტი (ESVS 2024): ღია დისექციისას მომატებულია თორმეტგოჯა ნაწლავისა და შარდსაწვეთების დაზიანების რისკი — ანატომიური შესაბამისობისას უპირატესია EVAR.'
        )
    aaa_eff = aaa_mm if aaa_mm is not None else getattr(ctx, 'aaa_mm_sat', None)
    if aaa_eff is not None:
        min_thresh = rule_value('AAA_FEMALE_MIN_MM') if patient.sex == 'F' else rule_value('AAA_MALE_MIN_MM')
        mm_fmt = int(aaa_eff) if aaa_eff == int(aaa_eff) else aaa_eff
        ctx.key_badge = f'{mm_fmt} მმ'
        ctx.key_badge_urgent = bool(aaa_eff >= giant_mm or aaa_morph == 'inflammatory_mycotic')
        if aaa_eff >= giant_mm:
            msg = f'გიგანტური ანევრიზმა ({aaa_eff} მმ >= {giant_mm} მმ) — მაღალი რუპტურის რისკი, დაჩქარებული მომზადება!'
            ctx.red_flags.append(msg)
            ctx.priority_flags.append(msg)
        elif aaa_mm is not None and aaa_mm < min_thresh:
            if aaa_morph == 'saccular':
                ctx.amber_flags.append(
                    f'ESVS 2024: საკულარული მორფოლოგია ({aaa_mm} მმ) — '
                    f'ოპერაციული ჩვენება დასაბუთებულია სტანდარტულ დიამეტრამდეც ({min_thresh} მმ).'
                )
            elif aaa_morph == 'rapid_growth':
                ctx.amber_flags.append(
                    f'ESVS 2024: სწრაფი ზრდის დინამიკა (≥10 მმ/წ, {aaa_mm} მმ) — '
                    f'სტანდარტულ ზღვრამდე ({min_thresh} მმ) ჩარევამდე გადაამოწმეთ გაზომვა ჭეშმარიტი ზრდის დასადასტურებლად (Class IIa/C).'
                )
            elif aaa_morph != 'inflammatory_mycotic' and not q.get('aaa_pain'):
                ctx.red_flags.append(
                    f'ფუზიფორმული ანევრიზმის დიამეტრი ({aaa_mm} მმ) სტანდარტულ ზღვარზე (< {min_thresh} მმ) ნაკლებია '
                    '(ESVS 2024 Class III) — გეგმური ოპერაციული ჩვენება გადაამოწმეთ ქირურგთან!'
                )
    if ctx.age is not None and ctx.age < young_age:
        ctx.amber_flags.append(
            f'ახალგაზრდა ასაკში გამოვლენილი აორტის ანევრიზმა ({ctx.age} წ. < {young_age} წ.) — ESVS 2024: '
            'გამოირიცხოს შემაერთებელი ქსოვილის გენეტიკური არტერიოპათია (Marfan / Loeys-Dietz / ვასკულიტი) და მთელი აორტის პათოლოგია.'
        )

    if aaa_neck == rule_value('AAA_HOSTILE_NECK'):
        if patient.op_subtype == 'evar':
            ctx.red_flags.append(
                'CTA-ზე Hostile Neck (ყელი <10–15 მმ / კუთხე >60° / თრომბი-კალციფიკაცია — IFU-ს გარეთ)! '
                'ESVS 2024 Class III: სტანდარტული EVAR არ არის რეკომენდებული — შეათანხმეთ ღია რეზექცია ან FEVAR ქირურგთან!'
            )
        elif patient.op_subtype == 'open':
            ctx.amber_flags.append(
                'CTA-ზე რთული პროქსიმალური ყელი (Hostile / იუქსტარენალური ყელი) ღია რეზექციაზე — '
                'დაიგეგმოს იუქსტა-/სუპრარენალური კლამპირების მზადყოფნა და თირკმლის ცივი პერფუზია (>25 წთ იშემიისას — ESVS 2024)!'
            )
        elif patient.op_subtype == 'tbd':
            ctx.amber_flags.append(
                'CTA-ზე Hostile Neck (IFU-ს გარეთ) — ტაქტიკის შერჩევისას სტანდარტული EVAR არ არის რეკომენდებული; განიხილეთ ღია რეზექცია ან FEVAR.'
            )
    elif patient.op_subtype == 'evar' and aaa_mm is not None and (
        aaa_neck == 'pending' or (s.get('aaa_iliac_access') or 'pending') == 'pending'
    ):
        ctx.amber_flags.append(
            'EVAR-ის დაგეგმვისას CTA-ზე შეაფასეთ აორტის პროქსიმალური ყელის (IFU) და თეძო-ბარძაყის მიდგომის (≥7 მმ) სტატუსი.'
        )
    if pop_scr == 'aneurysm_found':
        ctx.red_flags.append(
            'გამოვლინდა თანმხლები მუხლქვეშა/ბარძაყის ანევრიზმა (ESVS 2024: 10–15% თანაარსებობა AAA-სთან) — '
            'დისტალური თრომბოემბოლიის რისკი, დაგეგმეთ ეტაპობრივი/თანმხლები მკურნალობა ქირურგთან!'
        )


def eval_aaa(ctx):
    patient, q, s, q_filled = ctx.patient, ctx.q, ctx.s, ctx.q_filled
    aaa_morph = s.get('aaa_morphology') or 'fusiform'
    aaa_neck = s.get('aaa_neck_status') or 'pending'
    pop_scr = s.get('popliteal_screen_status') or 'pending'
    _eval_aaa_anatomy(ctx, aaa_morph, aaa_neck, pop_scr)

    if ctx.days > ctx.t_days and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        msg = f'გახანგრძლივებული წინასაოპერაციო მომზადება: გავიდა {ctx.days} დღე (სამიზნე ლიმიტი: {ctx.t_days} დღე) — რუპტურის რისკი!'
        ctx.red_flags.append(msg)
        ctx.priority_flags.append(msg)
    elif max(1, ctx.t_days - 2) <= ctx.days <= ctx.t_days and not patient.approved_at and not ctx.is_on_hold and ctx.is_active_case:
        ctx.amber_flags.append(f'მომზადების მე-{ctx.days} დღე ({ctx.t_days} დღიდან) — დააჩქარეთ დარჩენილი კვლევები.')

    cta_max_m, cta_large_m, cta_large_mm = rule_value('AAA_CTA_STALE_MONTHS')
    aaa_eff = ctx.aaa_mm if ctx.aaa_mm is not None else getattr(ctx, 'aaa_mm_sat', None)
    if ctx.cta_months is not None:
        if ctx.cta_months > cta_max_m or (ctx.cta_months > cta_large_m and aaa_eff is not None and aaa_eff >= cta_large_mm):
            ctx.red_flags.append(f'კტ-ანგიოგრაფია ხანდაზმულია ({ctx.cta_months} თვის წინანდელი) — შეათანხმეთ განმეორებითი CTA ქირურგთან.')
    if q_filled and not s.get('cta_disc'):
        ctx.amber_flags.append('აორტის კტ-ანგიოგრაფიის DICOM დისკი ჯერ არ არის მოტანილი / შემოწმებული.')

    if q_filled and q.get('cfs_level') == '5_plus' and patient.op_subtype == 'open':
        ctx.amber_flags.append('დაუძლურების მაღალი ინდექსი (CFS ≥ 5) — ESVS 2024-ით ღია რეზექციასთან შედარებით განიხილეთ EVAR ან რისკის გადაფასება.')
    if s.get('aaa_iliac_access') == 'narrow_calcified' and patient.op_subtype in ('evar', 'tbd'):
        ctx.amber_flags.append('CTA-ზე ვიწრო (<7 მმ) ან მძიმედ კალციფიცირებული თეძო-ბარძაყის მიდგომა — EVAR-ისას საჭიროა Iliac Conduit / მიდგომის დაგეგმვა.')
    elif s.get('aaa_iliac_access') == 'iliac_aneurysm':
        ctx.amber_flags.append('თანმხლები თეძოს არტერიის ანევრიზმა CTA-ზე — დასაგეგმია შიგნითა თეძოს არტერიის (IIA) შენარჩუნება.')
    if s.get('aaa_renal_vein_anom'):
        ctx.amber_flags.append('CTA-ზე რეტროაორტული მარცხენა თირკმლის ვენა / ცხენისებრი თირკმელი — საყურადღებოა აორტის ყელის დისექციისა და კლამპირებისას!')
    if q_filled and q.get('aaa_pedal_pulse') == 'reduced' and not getattr(ctx, 'is_rup_pre', False):
        ctx.needs_abi_check = True
        ctx.reflex_orders.append({
            'key': 'aaa_abi',
            'trigger': 'ტერფის პულსაცია მიღებისას დაქვეითებულია',
            'action': 'საბაზისო გოჯ-მხრის ინდექსი (ABI) + მუხლქვეშა არტერიების ექო-სკრინინგი ოპერაციამდე',
            'done': ctx.min_abi is not None,
        })
    ldl_target = rule_value('CEA_ANTIPLATELET_STATIN_MANDATORY')
    if not getattr(ctx, 'is_rup_pre', False) and not ctx.liver_3x and not getattr(ctx, 'eff_pregnant', False):
        if q_filled and not ctx.has_statin:
            ctx.amber_flags.append('პაციენტს არ უფიქსირდება სტატინი AAA-ს წინ (ESVS 2024: რეკომენდებულია გულ-სისხლძარღვთა რისკის შემცირება / ლიპიდების ოპტიმიზაცია).')
        if ctx.ldl_val is not None and ctx.ldl_val >= ldl_target:
            ctx.amber_flags.append(f'LDL ქოლესტერინი {ctx.ldl_val} მმოლ/ლ (>= {ldl_target} მმოლ/ლ) — განიხილეთ სტატინის დაწყება/ინტენსიფიკაცია ± ეზეტიმიბი (ESVS 2024).')

"""The first 50 quotes of the day (sent by the clinic; prepared with GPT).

Each is (text, author, source). Added once; edit or delete them on /quotes/.
"""
from django.db import migrations

QUOTES = [
    [
        "პაციენტის მოვლის საიდუმლო მასზე გულით ზრუნვაშია.",
        "ფრენსის უელდ პიბოდი",
        "https://jamanetwork.com/journals/jama/article-abstract/350340"
    ],
    [
        "თითქმის ყველაფერი ისევ ამუშავდება, თუ რამდენიმე წუთით გამორთავ. შენც.",
        "ენ ლამოტი",
        "https://nntrn.github.io/read-tedx/transcripts/anne_lamott_12_truths_i_learned_from_life_and_writing.html"
    ],
    [
        "ნამდვილი სიკეთე გვამაღლებს და გვეხმარება, ვისწავლოთ, აღმოვაჩინოთ და ვუმკურნალოთ.",
        "მარკ დევიდ სიგელი",
        "https://medicine.yale.edu/news-article/rudeness-and-the-power-of-kindness/"
    ],
    [
        "იმედი ჩიტია, სულში რომ შემოჯდება, უსიტყვოდ მღერის და არასოდეს ჩუმდება.",
        "ემილი დიკინსონი",
        "https://poets.org/anthology/hope-31"
    ],
    [
        "გამოცდილების ფასი ნანახის სიმრავლეში კი არა, დაკვირვებულ მზერაშია.",
        "უილიამ ოსლერი",
        "https://www.osler.org.uk/oslers-aphorisms"
    ],
    [
        "გულის სითბოს ვერაფერი შეედრება.",
        "ჯეინ ოსტინი",
        "https://en.wikisource.org/wiki/Page%3AAusten_-_Emma%2C_vol._II%2C_1816.djvu/260"
    ],
    [
        "ბუნების ტემპს მიჰყევი. მისი საიდუმლო მოთმინებაა.",
        "რალფ უოლდო ემერსონი",
        "https://rwema.org/blog/the-very-quotable-emerson"
    ],
    [
        "სწორედ ზრუნვის ადამიანური მხარე გვაგრძნობინებს, რომ ვიკურნებით.",
        "რანა აუდიში",
        "https://www.ama-assn.org/public-health/prevention-wellness/how-true-healing-begins-physician-s-words"
    ],
    [
        "დიდი საქმე ერთი იმპულსით არ კეთდება. ის ერთმანეთთან დაკავშირებული პატარა საქმეებისგან შედგება.",
        "ვინსენტ ვან გოგი",
        "https://quoteinvestigator.com/2015/12/14/impulse/"
    ],
    [
        "ერთმანეთის წყალობით და ერთმანეთისთვის ვცხოვრობთ. ცალ-ცალკე ცოტას შევძლებთ, ერთად კი — ბევრს.",
        "ჰელენ კელერი",
        "https://afb.org/about-afb/history/helen-keller/quotes/helen-keller-quotes-progress"
    ],
    [
        "დავსხდეთ პაციენტის პირისპირ, თვალის დონეზე. ვკითხოთ, რა აწუხებს, და კიდევ ერთი წუთი დავუთმოთ მის ბოლო კითხვას.",
        "ჯეი შაჰი",
        "https://med.stanford.edu/news/all-news/2025/06/graduation-2025.html"
    ],
    [
        "სიხარულის ნამცეცებს ნუ დასჯერდები.",
        "მერი ოლივერი",
        "https://readartdesk.com/features/dont-hesitate/"
    ],
    [
        "სიცილი ის მზეა, რომელიც ადამიანის სახიდან ზამთარს დევნის.",
        "ვიქტორ ჰიუგო",
        "https://wist.info/hugo-victor/1993/"
    ],
    [
        "შენს სიკეთეს შენზე მეტად არავინ იმსახურებს.",
        "კამსი ნვანგვუ",
        "https://med.stanford.edu/news/all-news/2023/08/white-coat-ceremony.html"
    ],
    [
        "მხოლოდ გულით ხედავ კარგად. არსებითს თვალი ვერ ხედავს.",
        "ანტუან დე სენტ-ეგზიუპერი",
        "https://djvu.online/file/1upUZzA42PMGI"
    ],
    [
        "რაც უნდა დამოუკიდებლად მუშაობა გვინდოდეს, რთულ საქმეს წარმატებით გუნდი ასრულებს.",
        "ატულ გავანდე",
        "https://blog.ted.com/atul-gawande-at-ted2012-2/"
    ],
    [
        "ცოტა გულისხმიერება და სხვებზე ცოტა ფიქრი ყველაფერს ცვლის.",
        "ალან ალექსანდერ მილნი",
        "https://baalroom-static.s3.amazonaws.com/media/files/books/2023/05/Winnie-the-pooh-A._A._Milne..pdf"
    ],
    [
        "ცოტა ხნით ამ სამყაროს სიკეთეს შევეფარები და თავს თავისუფლად ვგრძნობ.",
        "უენდელ ბერი",
        "https://onbeing.org/poetry/the-peace-of-wild-things/"
    ],
    [
        "ქარიშხლის არ მეშინია, რადგან ჩემი გემის მართვას ვსწავლობ.",
        "ლუიზა მეი ოლკოტი",
        "https://quoteatlas.info/sources/little-women/"
    ],
    [
        "ვინც ბუნების სილამაზეს აკვირდება, პოულობს ძალას, რომელიც მთელი სიცოცხლე გაჰყვება.",
        "რეიჩელ კარსონი",
        "https://archive.epa.gov/publicinvolvement/web/html/rachelcarsonsenseofwonder_article.html"
    ],
    [
        "ყველაზე დატვირთულ დროს განსაკუთრებით იგრძნობა ერთმანეთის უანგაროდ დახმარების სურვილი.",
        "ნათანიელ ფლემინგი",
        "https://med.stanford.edu/news/insights/2018/09/medicine-is-a-team-sport-and-thats-exactly-how-it-should-be.html"
    ],
    [
        "კარგად გაკეთებული საქმე კარგად ნათქვამ სიტყვას სჯობს.",
        "ბენჯამინ ფრანკლინი",
        "https://hamilton.gilderlehrman.org/supporting-document/poor-richards-almanac"
    ],
    [
        "ერთგულება ყურადღებით იწყება.",
        "მერი ოლივერი",
        "https://www.supersummary.com/upstream/important-quotes/"
    ],
    [
        "ამქვეყნად უსარგებლო ვერ იქნება ის, ვინც სხვას ცხოვრების ტვირთს უმსუბუქებს.",
        "ჩარლზ დიკენსი",
        "https://libquotes.com/charles-dickens/quote/lbk6j5y"
    ],
    [
        "რა მიხარია, რომ ისეთ სამყაროში ვცხოვრობ, სადაც ოქტომბერი არსებობს.",
        "ლუსი მოდ მონტგომერი",
        "https://gutenberg.net.au/ebooks14/1403271h.html"
    ],
    [
        "მთავარია, ყველაფერი ბოლომდე განიცადო. ახლა კი შენი კითხვებით იცხოვრე.",
        "რაინერ მარია რილკე",
        "https://www.alyve.org/english/docs/9.1/Rilke-Letters_to_a_Young_Poet.pdf"
    ],
    [
        "სხვაზე ზრუნვა თანასწორთა ურთიერთობაა. ამით სხვებიც ძლიერდებიან და ჩვენც.",
        "რეიჩელ ნაომი რემენი",
        "https://www.upaya.org/wp-content/uploads/2024/03/Helping-Fixing-or-Serving-Rachel-Naomi-Remen.pdf"
    ],
    [
        "ჩასუნთქვა სიამოვნებაა, ამოსუნთქვა კი ბედნიერება.",
        "ტიჩ ნატ ჰანი",
        "https://plumvillage.org/gems/event/Returning-to-Happiness-mLGojTNrUEoXG5ZSHT9jz3/preview?lang=en"
    ],
    [
        "მივხვდი, რომ სიმამაცე შიშის არქონა კი არა, მისი დაძლევაა.",
        "ნელსონ მანდელა",
        "https://graciousquotes.com/quotes-with-sources/nelson-mandela/"
    ],
    [
        "როცა საავადმყოფოში სიჩუმეა, ტელევიზორში საინტერესო არაფერია და საუბარს იშვიათად გაწყვეტინებენ, ადამიანებზე ბევრს იგებ.",
        "კაილ ედმონდსი",
        "https://www.ama-assn.org/practice-management/physician-health/dr-edmonds-moment-i-knew-medicine-was-my-calling"
    ],
    [
        "იმედი მაქვს, გამონახავთ გზებს, რომ მეტი დრო გავატაროთ პაციენტებთან და ერთმანეთთან, გულწრფელ საუბარსა და ურთიერთობაში.",
        "აბრაჰამ ვერგეზე",
        "https://med.stanford.edu/news/all-news/2014/06/prepared-text-of-commencement-remarks-by-abraham-verghese.html"
    ],
    [
        "შემოქმედებით უნარს ვერ ამოწურავ. რაც მეტად იყენებ, მით მეტი გაქვს.",
        "მაია ენჯელოუ",
        "https://quoteinvestigator.com/2014/03/03/creative-maya/"
    ],
    [
        "ფესვები მიწის ქვეშ ჯილდოს არ ითხოვენ იმისთვის, რომ ტოტებს ნაყოფი ასხია.",
        "რაბინდრანათ თაგორი",
        "https://en.wikisource.org/wiki/Stray_Birds"
    ],
    [
        "პაციენტებს ყველაზე მეტად ის ახსოვთ, რას გრძნობდნენ ჩვენთან ურთიერთობისას, და არა ის, რა გავაკეთეთ.",
        "კრისტინა სიუ",
        "https://hms.harvard.edu/news/hms-hsdm-class-2026-graduation-just-beginning"
    ],
    [
        "დაბრკოლება წინსვლაში გეხმარება. ის, რაც გზას გიღობავს, თავად იქცევა გზად.",
        "მარკუს ავრელიუსი",
        "https://quotle.info/who-said/the-impediment-to-action-advances-action-what-stands-in-the/"
    ],
    [
        "სამოთხე ჩვენს ფეხქვეშაც არის და თავს ზემოთაც.",
        "ჰენრი დევიდ თორო",
        "https://en.wikisource.org/wiki/Walden_%281854%29_Thoreau/The_Pond_in_Winter"
    ],
    [
        "სიკეთე თანამშრომლობას აძლიერებს და პაციენტების უსაფრთხოებას უწყობს ხელს.",
        "მარკ დევიდ სიგელი",
        "https://medicine.yale.edu/news-article/on-kindness/"
    ],
    [
        "ყურადღებით მოსმენისას პაციენტს ვაგრძნობინებთ, რომ ვაფასებთ იმას, რაც საკუთარ თავზე იცის. ასე ნდობაც ყალიბდება.",
        "რანა აუდიში და ლეონარდ ბერი",
        "https://www.physicianleaders.org/articles/the-importance-of-making-time-to-really-listen-to-your-patients"
    ],
    [
        "ყველაფერს შემიძლია წინააღმდეგობა გავუწიო, ცდუნების გარდა.",
        "ოსკარ უაილდი",
        "https://en.wikisource.org/wiki/Lady_Windermere%27s_Fan/Act_I"
    ],
    [
        "უკეთესობის მიღწევა შესაძლებელია. ამისთვის გენიოსობა არ არის საჭირო.",
        "ატულ გავანდე",
        "https://irp.nih.gov/catalyst/25/4/a-conversation-with-atul-gawande"
    ],
    [
        "აჰ, ნამდვილი სიმყუდროვისთვის სახლში დარჩენას არაფერი სჯობს.",
        "ჯეინ ოსტინი",
        "https://famousquotations.com/quotes/jane-austen-staying-home-comfort.html"
    ],
    [
        "გახსოვდეს, მნიშვნელოვანი მხოლოდ ერთი დროა — ახლა!",
        "ლევ ტოლსტოი",
        "https://en.wikisource.org/wiki/Page%3ATolstoy_-_Twenty-three_tales.djvu/303"
    ],
    [
        "ვფიქრობდი, თუ იდეა მართლაც ღირებულია, მისი განხორციელების გზაც უნდა არსებობდეს.",
        "ელიზაბეთ ბლექველი",
        "https://quotecloud.com/authors/elizabeth-blackwell"
    ],
    [
        "თუ პაციენტი ჩემგან იმ განცდით მიდის, რომ უხარია, მისი ექიმი მე ვიყავი, ამაზე დიდი სიხარული არ არსებობს.",
        "მარი ტაკეუჩი",
        "https://www.keio.ac.jp/en/med/feature/index-79/"
    ],
    [
        "როცა მიზანს მიაღწევ, საქმე დასრულებულია. სიხარული თავად კეთებაშია.",
        "უილიამ შექსპირი",
        "https://www.rsc.org.uk/troilus-and-cressida/about-the-play/famous-quotes"
    ],
    [
        "რისთვის ვცხოვრობთ, თუ ერთმანეთს ცხოვრებას არ შევუმსუბუქებთ?",
        "ჯორჯ ელიოტი",
        "https://quoteinvestigator.com/2019/08/01/live-for/"
    ],
    [
        "ყველაზე მეტად მაინც მადლიერებას ვგრძნობ.",
        "ოლივერ საქსი",
        "https://stsinfrastructures.org/sites/default/files/artifacts/media/pdf/0901_oliver_sacks_2015.pdf"
    ],
    [
        "ცოდნა შემთხვევით არ მოდის. მას მონდომებით უნდა ეძებო და გულმოდგინედ დაეუფლო.",
        "ებიგეილ ადამსი",
        "https://www.smu.edu/-/media/Site/StudentAffairs/DoS/StudentHandbook/1998-99_PCL.pdf"
    ],
    [
        "ზღვის სიმშვიდე ტალღებად ირხევა.",
        "რაბინდრანათ თაგორი",
        "https://en.wikisource.org/wiki/Stray_Birds"
    ],
    [
        "დღევანდელი დღით გაანათე ხვალინდელი.",
        "ელიზაბეთ ბარეტ ბრაუნინგი",
        "https://www.azquotes.com/quote/38852"
    ]
]


def add(apps, schema_editor):
    Quote = apps.get_model('mdf', 'Quote')
    have = set(Quote.objects.values_list('text', flat=True))
    for text, author, source in QUOTES:
        if text not in have:
            Quote.objects.create(text=text, author=author, source=source)


class Migration(migrations.Migration):
    dependencies = [('mdf', '0034_quotes')]
    operations = [migrations.RunPython(add, migrations.RunPython.noop)]

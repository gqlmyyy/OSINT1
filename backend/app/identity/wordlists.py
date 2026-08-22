"""Word lists backing the username rarity heuristic.

Data, not logic — kept in its own module so the scoring code stays readable and so an
operator can see exactly what the model considers "common" without reading an algorithm.

These lists are deliberately **small and high-value**. The goal is not to be a dictionary:
it is to recognise the handful of patterns that actually produce false identity matches —
a bare first name, a surname, a dictionary word, a role account, or a name with digits
stuck on the end. A handle that matches none of them is treated as unremarkable rather
than as rare, so an unknown word never *earns* confidence it has not demonstrated.

Everything here is lowercase and separator-free; callers normalise before lookup.
"""

from __future__ import annotations

#: Role, service and throwaway handles. Registered on nearly every platform by different
#: people, so agreement between two of them is close to worthless as identity evidence.
COMMON_HANDLES: frozenset[str] = frozenset(
    """
    admin administrator root user users guest test testing tester demo example sample
    info contact support help helpdesk sales marketing hello hi mail email webmaster
    postmaster noreply no-reply news team staff office official account accounts
    service services system sysadmin operator owner manager dev developer developers
    me myself you anyone someone nobody anon anonymous unknown null none undefined
    default temp temporary tmp backup old new main home index public private
    api bot bots robot daemon
    photography photos photo art artist music musician gaming gamer games official
    shop store business company inc ltd
    """.split()  # noqa: SIM905 - readability beats a 300-element literal
)

#: The most frequently used given names in English-speaking populations. A handle that is
#: simply a first name is shared by an enormous number of unrelated people.
GIVEN_NAMES: frozenset[str] = frozenset(
    """
    james robert john michael david william richard joseph thomas charles christopher
    daniel matthew anthony mark donald steven paul andrew joshua kenneth kevin brian
    george timothy ronald edward jason jeffrey ryan jacob gary nicholas eric jonathan
    stephen larry justin scott brandon benjamin samuel gregory alexander patrick frank
    raymond jack dennis jerry tyler aaron jose adam nathan henry zachary douglas peter
    kyle noah ethan jeremy walter christian keith roger terry austin sean gerald carl
    harold dylan arthur lawrence jordan jesse bryan billy bruce gabriel joe logan alan
    juan albert willie elijah wayne randy vincent mason roy ralph bobby russell bradley
    philip eugene
    mary patricia jennifer linda elizabeth barbara susan jessica sarah karen nancy lisa
    betty margaret sandra ashley kimberly emily donna michelle carol amanda dorothy
    melissa deborah stephanie rebecca sharon laura cynthia amy kathleen angela shirley
    anna brenda pamela emma nicole helen samantha katherine christine debra rachel carolyn
    janet catherine maria heather diane ruth julie olivia joyce virginia victoria kelly
    lauren christina joan evelyn judith megan andrea cheryl hannah jacqueline martha
    gloria teresa ann sara madison frances kathryn janice jean abigail alice julia judy
    sophia grace denise amber doris marilyn danielle beverly isabella theresa diana
    natalie brittany charlotte marie kayla alexis lori
    ahmed mohamed mohammed muhammad ali omar hassan hussein youssef yusuf ibrahim khaled
    mostafa mahmoud amir reza sara fatima aisha layla noor zainab mariam
    wei ming lei jun hui yan feng chen li wang zhang liu yang huang zhao
    ivan sergei dmitri alexei nikolai vladimir andrei mikhail olga natasha
    """.split()  # noqa: SIM905 - readability beats a 300-element literal
)

#: Short forms and nicknames. Handled separately because they are *more* contested than
#: the formal names they come from — far more people register `mike` than `michael` — and
#: leaving them out was letting `mike` score as an unrecognised, distinctive handle.
NICKNAMES: frozenset[str] = frozenset(
    """
    mike mikey dave davey dan danny chris matt matty tom tommy tony steve stevie ken
    kenny ben benny sam sammy greg alex al pat patty ray rob robbie rick ricky ron ronnie
    jim jimmy joe joey bob bobby bill billy will willy nick nicky andy tim timmy ted teddy
    ed eddie jeff jerry larry gary pete phil doug frank hank jack jake josh luke max nate
    rich richie sean shawn walt zach zack gabe vince marty russ stan curt kurt art artie
    charlie chuck rudy louie lou gus ike moe
    sue susie kate katie kathy liz lizzy beth bethy jen jenny jenn becky cathy cindy deb
    debbie ellie emmy gina jan janie jill jo joey judy kay kim kimmy lisa lori lynn maggie
    mandy meg megs mel nan nikki pam patty peggy robin sally sandy sara steph tammy tina
    val vicky vickie wendy trish trisha annie abby libby mimi dot dottie
    """.split()  # noqa: SIM905 - readability beats a 300-element literal
)

#: Common surnames. `johnsmith` is a person's name, not an identifier.
SURNAMES: frozenset[str] = frozenset(
    """
    smith johnson williams brown jones garcia miller davis rodriguez martinez hernandez
    lopez gonzalez wilson anderson thomas taylor moore jackson martin lee perez thompson
    white harris sanchez clark ramirez lewis robinson walker young allen king wright
    scott torres nguyen hill flores green adams nelson baker hall rivera campbell mitchell
    carter roberts gomez phillips evans turner diaz parker cruz edwards collins reyes
    stewart morris morales murphy cook rogers gutierrez ortiz morgan cooper peterson
    bailey reed kelly howard ramos kim cox ward richardson watson brooks chavez wood
    james bennett gray mendoza ruiz hughes price alvarez castillo sanders patel myers
    long ross foster jimenez powell jenkins perry russell sullivan bell coleman butler
    henderson barnes gonzales fisher vasquez simmons romero jordan patterson alexander
    hamilton graham reynolds griffin wallace moreno west cole hayes bryant herrera gibson
    ellis tran medina aguilar stevens murray ford castro marshall owens harrison fernandez
    mcdonald woods washington kennedy wells vargas henry chen freeman webb tucker guzman
    burns crawford olson simpson porter hunter gordon mendez silva shaw snyder mason
    dixon munoz hunt hicks holmes palmer wagner black robertson boyd rose stone salazar
    fox warren mills meyer rice schmidt garza daniels ferguson nichols stephens soto
    weaver ryan gardner payne grant dunn kelley spencer hawkins arnold pierce vazquez
    hansen peters santos hart bradley knight elliott cunningham duncan armstrong hudson
    carroll lane riley andrews ruiz burke lawrence matthews franklin dawson
    kumar sharma singh khan ahmed hussain ali malik shah rahman islam
    """.split()  # noqa: SIM905 - readability beats a 300-element literal
)

#: Ordinary English words that show up constantly inside handles. Not a dictionary — a
#: shortlist of the vocabulary handle-pickers actually reach for.
COMMON_WORDS: frozenset[str] = frozenset(
    """
    the and for you all one two three four five six seven eight nine ten out get can
    now day night time year world life love hate good bad best worst real true false
    big small little large tiny great super mega ultra hyper max min pro plus prime
    red blue green black white grey gray silver golden gold purple pink orange yellow
    dark light bright shadow ghost phantom spirit soul dream sleep wake dead alive
    fire ice water earth wind storm thunder lightning rain snow sun moon star sky cloud
    sea ocean river lake mountain forest tree flower rose lily garden field stone rock
    wolf fox bear lion tiger eagle hawk raven crow snake dragon phoenix panda cat dog
    bird fish shark whale horse deer owl rabbit mouse tiger monkey
    king queen prince princess lord lady knight warrior hunter ranger wizard mage witch
    ninja samurai pirate viking rebel outlaw hero villain angel demon devil god
    code coder codes coding hack hacker hacking dev devs build builder maker create
    creator design designer draw art arts craft craftsman work works working job
    data byte bit pixel cyber tech technology digital electric electro atomic quantum
    net web site page link node graph chain block stack queue cache core kernel
    game gamer gaming play player plays playing win winner lose loser score level up
    music song sing singer band rock pop jazz blues metal punk beat drum guitar piano
    book read reader write writer story tale novel poem word words text note notes
    food eat cook chef cake pizza coffee tea beer wine milk sugar salt sweet spicy
    run runner running walk jump fly flying ride rider drive driver race racer fast slow
    happy sad angry calm cool warm cold hot free lost found open close secret hidden
    first last next prev old new young ancient modern future past present forever never
    my your our their his her its self mine yours
    official real fake original copy clone twin double single solo team squad crew club
    """.split()  # noqa: SIM905 - readability beats a 300-element literal
)

#: Every list, for "does this token appear anywhere in our vocabulary" checks.
ALL_TOKENS: frozenset[str] = (
    COMMON_HANDLES | GIVEN_NAMES | NICKNAMES | SURNAMES | COMMON_WORDS
)

#: Name tokens specifically — used to detect `firstname + surname` handles.
NAME_TOKENS: frozenset[str] = GIVEN_NAMES | NICKNAMES | SURNAMES

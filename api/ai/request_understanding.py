
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ParseIssue:
    code: str
    message: str
    severity: str = "warning"


@dataclass
class PlanRequestUnderstanding:
    task_type: str
    total_days: int
    rest_days: Optional[int]
    rest_days_strict: bool
    inferred_goals: list[str]
    active_days: int = 0
    workouts_per_week: Optional[int] = None
    duration_weeks: Optional[int] = None
    duration_months: Optional[int] = None
    time_per_session_minutes: Optional[int] = None
    level: Optional[str] = None
    intensity: Optional[str] = None
    style: Optional[str] = None
    location: Optional[str] = None
    equipment: list[str] = field(default_factory=list)
    exclusions: list[str] = field(default_factory=list)
    body_focus: list[str] = field(default_factory=list)
    workout_types: list[str] = field(default_factory=list)
    health_flags: list[str] = field(default_factory=list)
    user_sex: Optional[str] = None
    user_age_group: Optional[str] = None
    schedule_preferences: list[str] = field(default_factory=list)
    preferred_rest_days: list[str] = field(default_factory=list)
    preferred_workout_days: list[str] = field(default_factory=list)
    notes: str = ""
    needs_clarification: bool = False
    issues: list[ParseIssue] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.rest_days is None:
            self.active_days = int(self.total_days)
        else:
            self.active_days = int(self.total_days) - int(self.rest_days)


WORD_TO_NUM: dict[str, int] = {}


WORD_TO_NUM['zero'] = 0

WORD_TO_NUM['ноль'] = 0

WORD_TO_NUM['nol'] = 0

WORD_TO_NUM['нуль'] = 0

WORD_TO_NUM['one'] = 1

WORD_TO_NUM['один'] = 1

WORD_TO_NUM['одна'] = 1

WORD_TO_NUM['одну'] = 1

WORD_TO_NUM['odin'] = 1

WORD_TO_NUM['odna'] = 1

WORD_TO_NUM['raz'] = 1

WORD_TO_NUM['two'] = 2

WORD_TO_NUM['два'] = 2

WORD_TO_NUM['две'] = 2

WORD_TO_NUM['dva'] = 2

WORD_TO_NUM['dve'] = 2

WORD_TO_NUM['three'] = 3

WORD_TO_NUM['три'] = 3

WORD_TO_NUM['tri'] = 3

WORD_TO_NUM['four'] = 4

WORD_TO_NUM['четыре'] = 4

WORD_TO_NUM['chetyre'] = 4

WORD_TO_NUM['five'] = 5

WORD_TO_NUM['пять'] = 5

WORD_TO_NUM['pyat'] = 5

WORD_TO_NUM["pyat'"] = 5

WORD_TO_NUM['six'] = 6

WORD_TO_NUM['шесть'] = 6

WORD_TO_NUM['shest'] = 6

WORD_TO_NUM['seven'] = 7

WORD_TO_NUM['семь'] = 7

WORD_TO_NUM['sem'] = 7

WORD_TO_NUM['eight'] = 8

WORD_TO_NUM['восемь'] = 8

WORD_TO_NUM['vosem'] = 8

WORD_TO_NUM['nine'] = 9

WORD_TO_NUM['девять'] = 9

WORD_TO_NUM['devyat'] = 9

WORD_TO_NUM['ten'] = 10

WORD_TO_NUM['десять'] = 10

WORD_TO_NUM['desyat'] = 10

WORD_TO_NUM['eleven'] = 11

WORD_TO_NUM['одиннадцать'] = 11

WORD_TO_NUM['odinnadcat'] = 11

WORD_TO_NUM['twelve'] = 12

WORD_TO_NUM['двенадцать'] = 12

WORD_TO_NUM['dvenadcat'] = 12

WORD_TO_NUM['thirteen'] = 13

WORD_TO_NUM['тринадцать'] = 13

WORD_TO_NUM['trinadcat'] = 13

WORD_TO_NUM['fourteen'] = 14

WORD_TO_NUM['четырнадцать'] = 14

WORD_TO_NUM['chetyrnadcat'] = 14

WORD_TO_NUM['fifteen'] = 15

WORD_TO_NUM['пятнадцать'] = 15

WORD_TO_NUM['pyatnadcat'] = 15

WORD_TO_NUM['sixteen'] = 16

WORD_TO_NUM['шестнадцать'] = 16

WORD_TO_NUM['shestnadcat'] = 16

WORD_TO_NUM['seventeen'] = 17

WORD_TO_NUM['семнадцать'] = 17

WORD_TO_NUM['semnadcat'] = 17

WORD_TO_NUM['eighteen'] = 18

WORD_TO_NUM['восемнадцать'] = 18

WORD_TO_NUM['vosemnadcat'] = 18

WORD_TO_NUM['nineteen'] = 19

WORD_TO_NUM['девятнадцать'] = 19

WORD_TO_NUM['devyatnadcat'] = 19

WORD_TO_NUM['twenty'] = 20

WORD_TO_NUM['двадцать'] = 20

WORD_TO_NUM['dvadcat'] = 20

WORD_TO_NUM['twenty one'] = 21

WORD_TO_NUM['двадцать один'] = 21

WORD_TO_NUM['dvadcat odin'] = 21

WORD_TO_NUM['twenty two'] = 22

WORD_TO_NUM['двадцать два'] = 22

WORD_TO_NUM['dvadcat dva'] = 22

WORD_TO_NUM['twenty three'] = 23

WORD_TO_NUM['двадцать три'] = 23

WORD_TO_NUM['dvadcat tri'] = 23

WORD_TO_NUM['twenty four'] = 24

WORD_TO_NUM['двадцать четыре'] = 24

WORD_TO_NUM['dvadcat chetyre'] = 24

WORD_TO_NUM['twenty five'] = 25

WORD_TO_NUM['двадцать пять'] = 25

WORD_TO_NUM['dvadcat pyat'] = 25

WORD_TO_NUM['twenty six'] = 26

WORD_TO_NUM['двадцать шесть'] = 26

WORD_TO_NUM['dvadcat shest'] = 26

WORD_TO_NUM['twenty seven'] = 27

WORD_TO_NUM['двадцать семь'] = 27

WORD_TO_NUM['dvadcat sem'] = 27

WORD_TO_NUM['twenty eight'] = 28

WORD_TO_NUM['двадцать восемь'] = 28

WORD_TO_NUM['dvadcat vosem'] = 28

WORD_TO_NUM['twenty nine'] = 29

WORD_TO_NUM['двадцать девять'] = 29

WORD_TO_NUM['dvadcat devyat'] = 29

WORD_TO_NUM['thirty'] = 30

WORD_TO_NUM['тридцать'] = 30

WORD_TO_NUM['tridcat'] = 30

WORD_TO_NUM['thirty one'] = 31

WORD_TO_NUM['тридцать один'] = 31

WORD_TO_NUM['tridcat odin'] = 31



GOAL_SYNONYMS: dict[str, list[str]] = {
    'lose_weight': [
        'lose weight',
        'weight loss',
        'fat loss',
        'burn fat',
        'drop weight',
        'slim down',
        'cut fat',
        'reduce fat',
        'reduce weight',
        'get lean',
        'lean out',
        'shed pounds',
        'похудеть',
        'похудение',
        'сбросить вес',
        'снизить вес',
        'сжечь жир',
        'убрать жир',
        'убрать лишний вес',
        'лишний вес',
        'жиросжигание',
        'сушка',
        'pohudet',
        'sbrosit ves',
        'snizit ves',
        'sjec zhyr',
        'zhirosjiganie',
        'fat burning',
        'cutting',
        'body recomposition',
    ],
    'build_muscle': [
        'build muscle',
        'gain muscle',
        'muscle gain',
        'hypertrophy',
        'bulk up',
        'get bigger',
        'increase muscle mass',
        'muscle building',
        'grow muscle',
        'lean bulk',
        'набрать мышечную массу',
        'набор массы',
        'нарастить мышцы',
        'мышечная масса',
        'гипертрофия',
        'стать больше',
        'массанабор',
        'nabor massy',
        'nabrat massu',
        'narastit myshcy',
        'gipertrofiya',
        'muscle mass',
        'size',
        'mass gain',
        'bulking',
    ],
    'maintain_weight': [
        'maintain weight',
        'weight maintenance',
        'stay the same',
        'keep weight',
        'поддерживать вес',
        'удержать вес',
        'сохранить вес',
        'поддержание веса',
        'podderzhivat ves',
        'uderzhat ves',
    ],
    'strength': [
        'get stronger',
        'increase strength',
        'strength',
        'powerlifting',
        'absolute strength',
        'functional strength',
        'max strength',
        'сила',
        'силовой',
        'увеличить силу',
        'стать сильнее',
        'силовая подготовка',
        'sila',
        'silovoy',
        'stat silnee',
    ],
    'endurance': [
        'endurance',
        'stamina',
        'cardio endurance',
        'aerobic capacity',
        'conditioning',
        'improve endurance',
        'work capacity',
        'выносливость',
        'кардио выносливость',
        'повысить выносливость',
        'vynoslivost',
        'kardio',
        'stamina',
    ],
    'mobility': [
        'mobility',
        'flexibility',
        'stretching',
        'range of motion',
        'joint health',
        'improve mobility',
        'more flexible',
        'мобильность',
        'гибкость',
        'растяжка',
        'подвижность',
        'улучшить подвижность',
        'mobilnost',
        'gibkost',
        'rastyazhka',
    ],
    'posture': [
        'posture',
        'better posture',
        'fix posture',
        'postural correction',
        'осанка',
        'улучшить осанку',
        'исправить осанку',
        'osanka',
        'ispravit osanku',
    ],
    'rehab': [
        'rehab',
        'rehabilitation',
        'recover from injury',
        'injury recovery',
        'therapeutic',
        'реабилитация',
        'восстановление',
        'после травмы',
        'после операции',
        'reabilitaciya',
        'vosstanovlenie',
    ],
    'athletic_performance': [
        'athletic performance',
        'sport performance',
        'explosiveness',
        'speed',
        'agility',
        'vertical jump',
        'athlete',
        'sports performance',
        'спортивная форма',
        'атлетизм',
        'скорость',
        'взрывная сила',
        'ловкость',
        'atletizm',
        'skorost',
        'vzryvnaya sila',
    ],
    'general_fitness': [
        'general fitness',
        'stay fit',
        'be healthy',
        'overall health',
        'fitness',
        'keep in shape',
        'look better',
        'feel better',
        'общая форма',
        'здоровье',
        'поддерживать форму',
        'быть в форме',
        'фитнес',
        'obshaya forma',
        'byt v forme',
    ],
}



BODY_FOCUS_SYNONYMS: dict[str, list[str]] = {
    'full_body': [
        'full body',
        'whole body',
        'all body',
        'все тело',
        'все тело',
        'полное тело',
        'fullbody',
    ],
    'upper_body': [
        'upper body',
        'верх тела',
        'верх',
        'up body',
        'verh tela',
    ],
    'lower_body': [
        'lower body',
        'низ тела',
        'ноги и ягодицы',
        'lower',
        'niz tela',
    ],
    'legs': [
        'legs',
        'leg day',
        'ноги',
        'nogi',
        'quads',
        'hamstrings',
        'calves',
    ],
    'glutes': [
        'glutes',
        'booty',
        'ягодицы',
        'ягодицы и ноги',
        'yagodicy',
    ],
    'back': [
        'back',
        'спина',
        'spina',
        'lats',
        'upper back',
        'lower back',
    ],
    'chest': [
        'chest',
        'грудь',
        'grud',
        'pecs',
    ],
    'shoulders': [
        'shoulders',
        'плечи',
        'plechi',
        'delts',
    ],
    'arms': [
        'arms',
        'руки',
        'ruki',
        'biceps',
        'triceps',
        'forearms',
    ],
    'core': [
        'core',
        'abs',
        'пресс',
        'живот',
        'korp',
        'press',
    ],
}



EQUIPMENT_SYNONYMS: dict[str, list[str]] = {
    'none': [
        'no equipment',
        'without equipment',
        'bodyweight only',
        'без оборудования',
        'bez oborudovaniya',
        'собственный вес',
    ],
    'dumbbells': [
        'dumbbells',
        'db',
        'гантели',
        'ganteli',
    ],
    'barbell': [
        'barbell',
        'штанга',
        'shtanga',
        'olympic bar',
    ],
    'kettlebell': [
        'kettlebell',
        'гиря',
        'girya',
    ],
    'bands': [
        'bands',
        'resistance bands',
        'эспандер',
        'резинки',
        'rezinki',
    ],
    'machines': [
        'machines',
        'machine',
        'тренажеры',
        'trenazhery',
    ],
    'pullup_bar': [
        'pull up bar',
        'pullup bar',
        'турник',
        'turnik',
    ],
    'bench': [
        'bench',
        'скамья',
        'skamya',
    ],
    'cable': [
        'cable',
        'cables',
        'блочный тренажер',
        'kanat',
        'krossover',
    ],
    'bike': [
        'bike',
        'велосипед',
        'велотренажер',
        'velotrenazher',
    ],
    'treadmill': [
        'treadmill',
        'дорожка',
        'begovaya dorozhka',
    ],
    'elliptical': [
        'elliptical',
        'орбитрек',
        'ellips',
    ],
    'jump_rope': [
        'jump rope',
        'rope',
        'скакалка',
        'skakalka',
    ],
    'trx': [
        'trx',
        'suspension trainer',
    ],
    'pool': [
        'pool',
        'swimming pool',
        'бассейн',
        'basseyn',
    ],
    'rower': [
        'rower',
        'rowing machine',
        'гребной тренажер',
        'grebnoy trenazher',
    ],
}



LOCATION_SYNONYMS: dict[str, list[str]] = {
    'home': [
        'home',
        'at home',
        'дом',
        'дома',
        'doma',
        'doma',
    ],
    'gym': [
        'gym',
        'fitness club',
        'зал',
        'спортзал',
        'gyм',
        'zal',
        'sportzal',
    ],
    'outdoor': [
        'outdoor',
        'outside',
        'street workout',
        'на улице',
        'улица',
        'park',
        'outdoors',
    ],
    'pool': [
        'pool',
        'бассейн',
        'basseyn',
    ],
    'studio': [
        'studio',
        'class studio',
        'студия',
        'grupovoy zal',
    ],
}



LEVEL_SYNONYMS: dict[str, list[str]] = {
    'beginner': [
        'beginner',
        'newbie',
        'novice',
        'starter',
        'начинающий',
        'новичок',
        'novichok',
        'nachinayushiy',
    ],
    'intermediate': [
        'intermediate',
        'medium',
        'mid',
        'средний',
        'опыт есть',
        'intermediate level',
        'sredniy',
    ],
    'advanced': [
        'advanced',
        'pro',
        'experienced',
        'athlete',
        'продвинутый',
        'опытный',
        'opytnyy',
        'prodvinutyy',
    ],
}



INTENSITY_SYNONYMS: dict[str, list[str]] = {
    'low': [
        'light',
        'easy',
        'low intensity',
        'легкий',
        'легкая',
        'не тяжело',
        'easy mode',
        'recovery intensity',
    ],
    'medium': [
        'medium',
        'moderate',
        'normal',
        'средний',
        'умеренный',
        'обычный',
    ],
    'high': [
        'hard',
        'intense',
        'high intensity',
        'advanced intensity',
        'тяжелый',
        'интенсивный',
        'жесткий',
    ],
}



PLAN_STYLE_SYNONYMS: dict[str, list[str]] = {
    'split': [
        'split',
        'bro split',
        'muscle split',
        'сплит',
        'split program',
    ],
    'push_pull_legs': [
        'push pull legs',
        'ppl',
        'push/pull/legs',
        'тяни толкай ноги',
    ],
    'upper_lower': [
        'upper lower',
        'верх низ',
        'upper/lower',
    ],
    'full_body': [
        'full body',
        'fullbody',
        'фулбоди',
        'все тело',
    ],
    'cardio_only': [
        'cardio only',
        'только кардио',
        'only cardio',
    ],
    'hybrid': [
        'hybrid',
        'mixed',
        'комбинированный',
        'смешанный',
    ],
}



EXCLUSION_SYNONYMS: dict[str, list[str]] = {
    'running': [
        'no running',
        'without running',
        'без бега',
        'не бегать',
        'bez bega',
    ],
    'jumping': [
        'no jumping',
        'without jumping',
        'без прыжков',
        'не прыгать',
        'bez pryzhkov',
    ],
    'squats': [
        'no squats',
        'без приседаний',
        'не делать присед',
        'bez prisedaniy',
    ],
    'pushups': [
        'no pushups',
        'без отжиманий',
        'bez otzhimaniy',
    ],
    'burpees': [
        'no burpees',
        'без берпи',
        'bez berpi',
    ],
    'equipment': [
        'without equipment',
        'без оборудования',
        'bodyweight only',
    ],
}



TASK_TYPE_SYNONYMS: dict[str, list[str]] = {
    'weekly_plan': [
        'weekly plan',
        'week plan',
        'еженедельный план',
        'план на неделю',
        'недельный план',
    ],
    'monthly_plan': [
        'monthly plan',
        'план на месяц',
        'месячный план',
    ],
    'meal_plan': [
        'meal plan',
        'nutrition plan',
        'план питания',
        'рацион',
    ],
    'workout_plan': [
        'workout plan',
        'training plan',
        'программа тренировок',
        'план тренировок',
    ],
    'running_plan': [
        'running plan',
        'беговой план',
        'plan for running',
    ],
    'walking_plan': [
        'walking plan',
        'план ходьбы',
        'step plan',
    ],
    'rehab_plan': [
        'rehab plan',
        'восстановительный план',
        'rehabilitation plan',
    ],
}



AGE_MARKERS: dict[str, list[str]] = {
    'teen': [
        'teen',
        'teenager',
        'подросток',
        'подростковый',
    ],
    'adult': [
        'adult',
        'взрослый',
        'для взрослого',
    ],
    'senior': [
        'senior',
        'elderly',
        'пожилой',
        'для пожилого',
    ],
}



SEX_MARKERS: dict[str, list[str]] = {
    'male': [
        'male',
        'man',
        'men',
        'мужчина',
        'мужской',
        'парень',
    ],
    'female': [
        'female',
        'woman',
        'women',
        'женщина',
        'женский',
        'девушка',
    ],
}



HEALTH_FLAGS: dict[str, list[str]] = {
    'knee_pain': [
        'knee pain',
        'bad knees',
        'болят колени',
        'колени болят',
        'проблемы с коленями',
    ],
    'back_pain': [
        'back pain',
        'болит спина',
        'проблемы со спиной',
        'грыжа',
    ],
    'shoulder_pain': [
        'shoulder pain',
        'болит плечо',
        'плечи болят',
    ],
    'hypertension': [
        'high blood pressure',
        'hypertension',
        'давление',
        'гипертония',
    ],
    'diabetes': [
        'diabetes',
        'диабет',
    ],
    'pregnancy': [
        'pregnant',
        'pregnancy',
        'беременность',
        'беременна',
    ],
}



WORKOUT_TYPE_SYNONYMS: dict[str, list[str]] = {
    'strength_training': [
        'strength training',
        'weights',
        'силовые',
        'силовая тренировка',
    ],
    'cardio': [
        'cardio',
        'кардио',
        'aerobic',
    ],
    'hiit': [
        'hiit',
        'interval training',
        'интервальная тренировка',
        'табата',
    ],
    'pilates': [
        'pilates',
        'пилатес',
    ],
    'yoga': [
        'yoga',
        'йога',
    ],
    'stretching': [
        'stretching',
        'растяжка',
        'mobility flow',
    ],
    'walking': [
        'walking',
        'ходьба',
        'walk',
    ],
    'running': [
        'running',
        'jogging',
        'бег',
        'пробежка',
    ],
    'cycling': [
        'cycling',
        'bike',
        'велосипед',
    ],
    'swimming': [
        'swimming',
        'плавание',
    ],
    'boxing': [
        'boxing',
        'box',
        'бокс',
    ],
    'crossfit': [
        'crossfit',
        'кроссфит',
    ],
    'dance': [
        'dance',
        'танцы',
    ],
}



SCHEDULE_HINTS: dict[str, list[str]] = {
    'monday': [
        'monday',
        'mon',
        'понедельник',
        'пн',
        'pn',
    ],
    'tuesday': [
        'tuesday',
        'tue',
        'вторник',
        'вт',
        'vt',
    ],
    'wednesday': [
        'wednesday',
        'wed',
        'среда',
        'ср',
        'sr',
    ],
    'thursday': [
        'thursday',
        'thu',
        'четверг',
        'чт',
        'cht',
    ],
    'friday': [
        'friday',
        'fri',
        'пятница',
        'пт',
        'pt',
    ],
    'saturday': [
        'saturday',
        'sat',
        'суббота',
        'сб',
        'sb',
    ],
    'sunday': [
        'sunday',
        'sun',
        'воскресенье',
        'вс',
        'vs',
    ],
}



PREFERENCE_SYNONYMS: dict[str, list[str]] = {
    'short_sessions': [
        'short workouts',
        'short sessions',
        'quick workouts',
        'короткие тренировки',
    ],
    'long_sessions': [
        'long workouts',
        'long sessions',
        'длинные тренировки',
    ],
    'simple_exercises': [
        'simple exercises',
        'beginner friendly',
        'простые упражнения',
        'без сложных упражнений',
    ],
    'progressive_overload': [
        'progressive overload',
        'increase load',
        'прогрессия нагрузки',
    ],
    'low_impact': [
        'low impact',
        'joint friendly',
        'без ударной нагрузки',
        'щадящая нагрузка',
    ],
    'home_friendly': [
        'home friendly',
        'for home',
        'для дома',
    ],
    'gym_friendly': [
        'gym based',
        'for gym',
        'для зала',
    ],
}



CALORIE_GOAL_SYNONYMS: dict[str, list[str]] = {
    'deficit': [
        'calorie deficit',
        'дефицит калорий',
        'eat less',
        'fat loss nutrition',
    ],
    'maintenance': [
        'maintenance calories',
        'поддержание калорий',
        'eat at maintenance',
    ],
    'surplus': [
        'calorie surplus',
        'профицит калорий',
        'eat more',
        'mass gain nutrition',
    ],
}



TIME_PER_SESSION_PATTERNS: dict[str, list[str]] = {
    '15': [
        '15 min',
        '15 mins',
        '15 minutes',
        '15 минут',
        '15 minuty',
    ],
    '20': [
        '20 min',
        '20 mins',
        '20 minutes',
        '20 минут',
        '20 minuty',
    ],
    '25': [
        '25 min',
        '25 mins',
        '25 minutes',
        '25 минут',
        '25 minuty',
    ],
    '30': [
        '30 min',
        '30 mins',
        '30 minutes',
        '30 минут',
        '30 minuty',
        'half hour',
        'полчаса',
    ],
    '35': [
        '35 min',
        '35 mins',
        '35 minutes',
        '35 минут',
    ],
    '40': [
        '40 min',
        '40 mins',
        '40 minutes',
        '40 минут',
    ],
    '45': [
        '45 min',
        '45 mins',
        '45 minutes',
        '45 минут',
    ],
    '50': [
        '50 min',
        '50 mins',
        '50 minutes',
        '50 минут',
    ],
    '60': [
        '60 min',
        '60 mins',
        '60 minutes',
        '1 hour',
        '1 hr',
        '1 час',
        'час',
    ],
    '75': [
        '75 min',
        '75 minutes',
        '1h 15m',
        '1 час 15 минут',
    ],
    '90': [
        '90 min',
        '90 minutes',
        '1.5 hour',
        '1 час 30 минут',
    ],
}



REST_MARKERS = [
    'rest',
    'rest day',
    'rest days',
    'day off',
    'days off',
    'off day',
    'off days',
    'relax',
    'relax day',
    'recovery off',
    'full rest',
    'complete rest',
    'full day off',
    'выходной',
    'выходные',
    'день отдыха',
    'дни отдыха',
    'отдых',
    'полный отдых',
    'day relax',
    'otdyh',
    'otdykh',
    'vyhodnoy',
    'vyhodnye',
    'den otdyha',
    'dni otdyha',
    'без тренировки',
    'не тренироваться',
    'перерыв',
    'pause day',
    'off',
    'break day',
    'resting day',
    'do not train',
    'no training day',
    'weekend rest',
    'inactive day',
]



RECOVERY_MARKERS = [
    'recovery',
    'recovery day',
    'recovery days',
    'light recovery',
    'active recovery',
    'mobility day',
    'stretch day',
    'easy day',
    'deload',
    'deload day',
    'restorative',
    'восстановление',
    'день восстановления',
    'легкий день',
    'разгрузочный день',
    'восстановительный день',
    'reabilitation day',
    'soft day',
]



ACTIVE_MARKERS = [
    'workout',
    'training',
    'session',
    'exercise',
    'active day',
    'gym day',
    'cardio day',
    'силовая',
    'тренировка',
    'активный день',
    'занятие',
    'сессия',
    'нагрузка',
    'work',
    'train',
    'lift',
    'run',
    'walk',
    'swim',
    'box',
    'cycle',
    'pilates',
    'yoga',
]



WEEKLY_MARKERS = [
    'weekly',
    'week',
    '7 day',
    '7-day',
    'for a week',
    'each week',
    'per week',
    'на неделю',
    'недельный',
    'еженедельный',
    'за неделю',
    'неделя',
    'week long',
    'one week',
    '1 week',
    'sedmitsa',
    'weekly plan',
]



MONTHLY_MARKERS = [
    'monthly',
    'month',
    '30 day',
    '30-day',
    'for a month',
    'per month',
    'на месяц',
    'месячный',
    'месяц',
]



NEGATION_MARKERS = [
    'no',
    'without',
    'dont',
    "don't",
    'do not',
    'avoid',
    'exclude',
    'except',
    'без',
    'не',
    'исключить',
    'избегать',
    'кроме',
    'убрать',
]



STRICTNESS_MARKERS = [
    'exactly',
    'strictly',
    'only',
    'must be',
    'not more not less',
    'literally',
    'ровно',
    'строго',
    'только',
    'именно',
    'не больше не меньше',
]



CANONICAL_GOAL_MAP: dict[str, str] = {}
for _goal_key, _goal_values in GOAL_SYNONYMS.items():
    for _value in _goal_values:
        CANONICAL_GOAL_MAP[_value] = _goal_key

CANONICAL_BODY_FOCUS_MAP: dict[str, str] = {}
for _focus_key, _focus_values in BODY_FOCUS_SYNONYMS.items():
    for _value in _focus_values:
        CANONICAL_BODY_FOCUS_MAP[_value] = _focus_key

CANONICAL_EQUIPMENT_MAP: dict[str, str] = {}
for _equipment_key, _equipment_values in EQUIPMENT_SYNONYMS.items():
    for _value in _equipment_values:
        CANONICAL_EQUIPMENT_MAP[_value] = _equipment_key

CANONICAL_LOCATION_MAP: dict[str, str] = {}
for _location_key, _location_values in LOCATION_SYNONYMS.items():
    for _value in _location_values:
        CANONICAL_LOCATION_MAP[_value] = _location_key

CANONICAL_LEVEL_MAP: dict[str, str] = {}
for _level_key, _level_values in LEVEL_SYNONYMS.items():
    for _value in _level_values:
        CANONICAL_LEVEL_MAP[_value] = _level_key

CANONICAL_INTENSITY_MAP: dict[str, str] = {}
for _intensity_key, _intensity_values in INTENSITY_SYNONYMS.items():
    for _value in _intensity_values:
        CANONICAL_INTENSITY_MAP[_value] = _intensity_key

CANONICAL_STYLE_MAP: dict[str, str] = {}
for _style_key, _style_values in PLAN_STYLE_SYNONYMS.items():
    for _value in _style_values:
        CANONICAL_STYLE_MAP[_value] = _style_key

CANONICAL_EXCLUSION_MAP: dict[str, str] = {}
for _exclusion_key, _exclusion_values in EXCLUSION_SYNONYMS.items():
    for _value in _exclusion_values:
        CANONICAL_EXCLUSION_MAP[_value] = _exclusion_key

CANONICAL_TASK_TYPE_MAP: dict[str, str] = {}
for _task_key, _task_values in TASK_TYPE_SYNONYMS.items():
    for _value in _task_values:
        CANONICAL_TASK_TYPE_MAP[_value] = _task_key

CANONICAL_AGE_MAP: dict[str, str] = {}
for _age_key, _age_values in AGE_MARKERS.items():
    for _value in _age_values:
        CANONICAL_AGE_MAP[_value] = _age_key

CANONICAL_SEX_MAP: dict[str, str] = {}
for _sex_key, _sex_values in SEX_MARKERS.items():
    for _value in _sex_values:
        CANONICAL_SEX_MAP[_value] = _sex_key

CANONICAL_HEALTH_MAP: dict[str, str] = {}
for _health_key, _health_values in HEALTH_FLAGS.items():
    for _value in _health_values:
        CANONICAL_HEALTH_MAP[_value] = _health_key

CANONICAL_WORKOUT_TYPE_MAP: dict[str, str] = {}
for _workout_key, _workout_values in WORKOUT_TYPE_SYNONYMS.items():
    for _value in _workout_values:
        CANONICAL_WORKOUT_TYPE_MAP[_value] = _workout_key

CANONICAL_PREFERENCE_MAP: dict[str, str] = {}
for _pref_key, _pref_values in PREFERENCE_SYNONYMS.items():
    for _value in _pref_values:
        CANONICAL_PREFERENCE_MAP[_value] = _pref_key

CANONICAL_CALORIE_MAP: dict[str, str] = {}
for _cal_key, _cal_values in CALORIE_GOAL_SYNONYMS.items():
    for _value in _cal_values:
        CANONICAL_CALORIE_MAP[_value] = _cal_key

DAY_UNIT_PATTERN = r"(?:day|days|день|дня|дней|днем|дн(?:я|ей)?|den|dnya|dney)"
WEEK_UNIT_PATTERN = r"(?:week|weeks|недел[яиюеь]?|нед|nedel(?:ya|i|e|u)?|ned)"
MONTH_UNIT_PATTERN = r"(?:month|months|месяц(?:а|ев)?|мес(?:яц)?|mesyac|mesyaca|mesyatsev|mes)"
SESSION_UNIT_PATTERN = r"(?:session|sessions|workout|workouts|training|trainings|тренировка|тренировки|занятие|занятия)"
MINUTE_UNIT_PATTERN = r"(?:min|mins|minute|minutes|мин|минут|минута|минуты)"
HOUR_UNIT_PATTERN = r"(?:hour|hours|hr|hrs|час|часа|часов)"
DAY_NAME_ORDER = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _normalized_text(text: str) -> str:
    value = str(text or "")
    value = value.replace("ё", "е")
    value = value.replace("—", "-")
    value = value.replace("–", "-")
    value = value.replace("/", " / ")
    value = value.replace("|", " | ")
    value = re.sub(r"[_]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value


def _clean_phrase(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower().replace("ё", "е"))


def _safe_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        return int(str(value).strip())
    except Exception:
        return None


def _unique_keep_order(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = _clean_phrase(value)
        if not cleaned:
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        out.append(cleaned)
    return out


def _find_phrases(text: str, candidates: list[str]) -> list[str]:
    normalized = _normalized_text(text)
    found: list[str] = []
    for phrase in candidates:
        cleaned = _clean_phrase(phrase)
        if not cleaned:
            continue
        if re.search(rf"(?<!\w){re.escape(cleaned)}(?!\w)", normalized):
            found.append(cleaned)
    return _unique_keep_order(found)


def _find_mapping_hits(text: str, mapping: dict[str, list[str]]) -> list[str]:
    normalized = _normalized_text(text)
    hits: list[str] = []
    for key, values in mapping.items():
        for value in values:
            cleaned = _clean_phrase(value)
            if re.search(rf"(?<!\w){re.escape(cleaned)}(?!\w)", normalized):
                hits.append(key)
                break
    return _unique_keep_order(hits)


def _contains_any(text: str, candidates: list[str]) -> bool:
    normalized = _normalized_text(text)
    for phrase in candidates:
        cleaned = _clean_phrase(phrase)
        if cleaned and re.search(rf"(?<!\w){re.escape(cleaned)}(?!\w)", normalized):
            return True
    return False


def _extract_explicit_numbers(text: str) -> list[int]:
    normalized = _normalized_text(text)
    values = [int(item) for item in re.findall(r"\b\d+\b", normalized)]
    return values


def _extract_number_words(text: str) -> list[tuple[str, int]]:
    normalized = _normalized_text(text)
    matches: list[tuple[str, int]] = []
    for phrase, number in WORD_TO_NUM.items():
        cleaned = _clean_phrase(phrase)
        if cleaned and re.search(rf"(?<!\w){re.escape(cleaned)}(?!\w)", normalized):
            matches.append((cleaned, number))
    matches.sort(key=lambda item: (-len(item[0]), item[1]))
    return matches


def _extract_all_number_candidates(text: str) -> list[int]:
    values = _extract_explicit_numbers(text)
    values.extend(number for _, number in _extract_number_words(text))
    return values


def _extract_number_near_unit(text: str, unit_pattern: str) -> Optional[int]:
    normalized = _normalized_text(text)

    direct = re.search(rf"\b(\d+)\s*{unit_pattern}\b", normalized)
    if direct:
        return int(direct.group(1))

    reverse = re.search(rf"\b{unit_pattern}\s*(\d+)\b", normalized)
    if reverse:
        return int(reverse.group(1))

    for phrase, number in _extract_number_words(normalized):
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)\s*{unit_pattern}\b", normalized):
            return int(number)
        if re.search(rf"\b{unit_pattern}\s*(?<!\w){re.escape(phrase)}(?!\w)", normalized):
            return int(number)
    return None


def _extract_age_number(text: str) -> Optional[int]:
    normalized = _normalized_text(text)
    patterns = [
        r"\b(\d{1,2})\s*(?:years old|year old|yo|лет|года|год)\b",
        r"\bage\s*(\d{1,2})\b",
        r"\bвозраст\s*(\d{1,2})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            value = int(match.group(1))
            if 5 <= value <= 99:
                return value
    return None


def _detect_weekly(text: str) -> bool:
    normalized = _normalized_text(text)
    if _contains_any(normalized, WEEKLY_MARKERS):
        return True
    if re.search(r"\b7\s*[- ]?day\b", normalized):
        return True
    return False


def _detect_monthly(text: str) -> bool:
    normalized = _normalized_text(text)
    if _contains_any(normalized, MONTHLY_MARKERS):
        return True
    if re.search(r"\b30\s*[- ]?day\b", normalized):
        return True
    return False


def _extract_task_type(text: str) -> str:
    normalized = _normalized_text(text)

    if _detect_monthly(normalized):
        return "monthly_plan"
    if _detect_weekly(normalized):
        return "weekly_plan"

    for phrase, task in CANONICAL_TASK_TYPE_MAP.items():
        if re.search(rf"(?<!\w){re.escape(_clean_phrase(phrase))}(?!\w)", normalized):
            return task

    if _find_mapping_hits(normalized, WORKOUT_TYPE_SYNONYMS):
        return "workout_plan"
    return "plan_generation"


def _extract_duration_days(text: str, *, weekly_default: bool) -> int:
    normalized = _normalized_text(text)

    if _detect_monthly(normalized):
        explicit_months = _extract_number_near_unit(normalized, MONTH_UNIT_PATTERN)
        if explicit_months is not None:
            return max(1, min(explicit_months * 30, 365))
        return 30

    duration_week_patterns = [
        rf"\bfor\s+(\d+)\s*{WEEK_UNIT_PATTERN}\b",
        rf"\b(\d+)\s*[- ]?week\s+plan\b",
        rf"\bplan\s+for\s+(\d+)\s*{WEEK_UNIT_PATTERN}\b",
        rf"\bна\s+(\d+)\s*недел\w*\b",
    ]
    for pattern in duration_week_patterns:
        match = re.search(pattern, normalized)
        if match:
            return max(1, min(int(match.group(1)) * 7, 365))

    for phrase, number in _extract_number_words(normalized):
        word_duration_week_patterns = [
            rf"\bfor\s+(?<!\w){re.escape(phrase)}(?!\w)\s*{WEEK_UNIT_PATTERN}\b",
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*[- ]?week\s+plan\b",
            rf"\bplan\s+for\s+(?<!\w){re.escape(phrase)}(?!\w)\s*{WEEK_UNIT_PATTERN}\b",
            rf"\bна\s+(?<!\w){re.escape(phrase)}(?!\w)\s*недел\w*\b",
        ]
        for pattern in word_duration_week_patterns:
            if re.search(pattern, normalized):
                return max(1, min(int(number) * 7, 365))

    explicit_days = _extract_number_near_unit(normalized, DAY_UNIT_PATTERN)
    if explicit_days is not None:
        return max(1, min(explicit_days, 365))

    if _detect_weekly(normalized):
        return 7
    return 7 if weekly_default else 30


def _extract_duration_weeks(text: str, total_days: int) -> Optional[int]:
    normalized = _normalized_text(text)
    patterns = [
        rf"\bfor\s+(\d+)\s*{WEEK_UNIT_PATTERN}\b",
        rf"\b(\d+)\s*[- ]?week\s+plan\b",
        rf"\bplan\s+for\s+(\d+)\s*{WEEK_UNIT_PATTERN}\b",
        rf"\bна\s+(\d+)\s*недел\w*\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))
    for phrase, number in _extract_number_words(normalized):
        word_patterns = [
            rf"\bfor\s+(?<!\w){re.escape(phrase)}(?!\w)\s*{WEEK_UNIT_PATTERN}\b",
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*[- ]?week\s+plan\b",
            rf"\bplan\s+for\s+(?<!\w){re.escape(phrase)}(?!\w)\s*{WEEK_UNIT_PATTERN}\b",
            rf"\bна\s+(?<!\w){re.escape(phrase)}(?!\w)\s*недел\w*\b",
        ]
        for pattern in word_patterns:
            if re.search(pattern, normalized):
                return int(number)
    if total_days % 7 == 0 and _detect_weekly(normalized):
        return total_days // 7
    return None


def _extract_duration_months(text: str, total_days: int) -> Optional[int]:
    normalized = _normalized_text(text)
    explicit_months = _extract_number_near_unit(normalized, MONTH_UNIT_PATTERN)
    if explicit_months is not None:
        return explicit_months
    if total_days % 30 == 0 and _detect_monthly(normalized):
        return total_days // 30
    return None


def _is_negated_context(text: str, phrase: str) -> bool:
    normalized = _normalized_text(text)
    cleaned = _clean_phrase(phrase)
    if not cleaned:
        return False
    patterns = [
        rf"(?:{'|'.join(re.escape(item) for item in NEGATION_MARKERS)})\s+{re.escape(cleaned)}",
        rf"{re.escape(cleaned)}\s+(?:is\s+not|not|не|без)",
    ]
    for pattern in patterns:
        if re.search(pattern, normalized):
            return True
    return False


def _extract_rest_days(text: str) -> tuple[Optional[int], bool]:
    normalized = _normalized_text(text)
    rest_token = r"(?:rest|off|relax|выходн(?:ой|ых|ые|ым|ыми|ом)?|отдых(?:а|ом|у)?|otdyh|otdykh|vyhodn(?:oy|yi|ye|yh)?)"
    patterns = [
        rf"\b(\d+)\s*{DAY_UNIT_PATTERN}\s*{rest_token}\b",
        rf"\b(\d+)\s*{rest_token}\s*{DAY_UNIT_PATTERN}\b",
        rf"\b(\d+)\s*(?:день|дня|дней|днем)\s*(?:отдыха)\b",
        rf"\b(\d+)\s*{rest_token}\b",
        rf"\b{rest_token}\s*(\d+)\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1)), True

    for phrase, value in _extract_number_words(normalized):
        word_patterns = [
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*{DAY_UNIT_PATTERN}\s*{rest_token}\b",
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*{rest_token}\s*{DAY_UNIT_PATTERN}\b",
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*(?:день|дня|дней|днем)\s*(?:отдыха)\b",
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*{rest_token}\b",
            rf"\b{rest_token}\s*(?<!\w){re.escape(phrase)}(?!\w)",
        ]
        for pattern in word_patterns:
            if re.search(pattern, normalized):
                return int(value), True

    if _contains_any(normalized, REST_MARKERS):
        if _contains_any(normalized, STRICTNESS_MARKERS):
            return 1, True
        return 1, False

    return None, False


def _extract_workouts_per_week(text: str, total_days: int, rest_days: Optional[int]) -> Optional[int]:
    normalized = _normalized_text(text)

    patterns = [
        rf"\b(\d+)\s*(?:times|x)?\s*(?:per|a)\s*week\b",
        rf"\b(\d+)\s*{SESSION_UNIT_PATTERN}\s*(?:per|a)\s*week\b",
        rf"\b(\d+)\s*(?:раз(?:а)?|трениров(?:ки|ок))\s*в\s*недел\w*\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            return int(match.group(1))

    for phrase, value in _extract_number_words(normalized):
        word_patterns = [
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*(?:times|x)?\s*(?:per|a)\s*week\b",
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*{SESSION_UNIT_PATTERN}\s*(?:per|a)\s*week\b",
            rf"(?<!\w){re.escape(phrase)}(?!\w)\s*(?:раз(?:а)?|трениров(?:ки|ок))\s*в\s*недел\w*\b",
        ]
        for pattern in word_patterns:
            if re.search(pattern, normalized):
                return int(value)

    if rest_days is not None and total_days == 7:
        return max(0, total_days - rest_days)
    return None


def _extract_time_per_session_minutes(text: str) -> Optional[int]:
    normalized = _normalized_text(text)

    for minutes, phrases in TIME_PER_SESSION_PATTERNS.items():
        for phrase in phrases:
            cleaned = _clean_phrase(phrase)
            if re.search(rf"(?<!\w){re.escape(cleaned)}(?!\w)", normalized):
                return int(minutes)

    match = re.search(rf"\b(\d+)\s*{MINUTE_UNIT_PATTERN}\b", normalized)
    if match:
        minutes = int(match.group(1))
        if 5 <= minutes <= 240:
            return minutes

    hour_match = re.search(rf"\b(\d+)\s*{HOUR_UNIT_PATTERN}\b", normalized)
    if hour_match:
        hours = int(hour_match.group(1))
        if 1 <= hours <= 5:
            return hours * 60

    mixed_match = re.search(rf"\b(\d+)\s*{HOUR_UNIT_PATTERN}\s*(\d+)\s*{MINUTE_UNIT_PATTERN}\b", normalized)
    if mixed_match:
        hours = int(mixed_match.group(1))
        minutes = int(mixed_match.group(2))
        total = hours * 60 + minutes
        if 5 <= total <= 240:
            return total

    return None


def _extract_level(text: str, meta: Optional[dict[str, Any]] = None) -> Optional[str]:
    normalized = _normalized_text(text)
    for phrase, level in CANONICAL_LEVEL_MAP.items():
        if re.search(rf"(?<!\w){re.escape(_clean_phrase(phrase))}(?!\w)", normalized):
            return level
    if isinstance(meta, dict):
        meta_level = meta.get("level")
        if meta_level:
            cleaned = _clean_phrase(meta_level)
            if cleaned in CANONICAL_LEVEL_MAP:
                return CANONICAL_LEVEL_MAP[cleaned]
            return cleaned
    return None


def _extract_intensity(text: str, meta: Optional[dict[str, Any]] = None) -> Optional[str]:
    normalized = _normalized_text(text)
    for phrase, intensity in CANONICAL_INTENSITY_MAP.items():
        if re.search(rf"(?<!\w){re.escape(_clean_phrase(phrase))}(?!\w)", normalized):
            return intensity
    if isinstance(meta, dict):
        meta_intensity = meta.get("intensity")
        if meta_intensity:
            cleaned = _clean_phrase(meta_intensity)
            if cleaned in CANONICAL_INTENSITY_MAP:
                return CANONICAL_INTENSITY_MAP[cleaned]
            return cleaned
    return None


def _extract_style(text: str, meta: Optional[dict[str, Any]] = None) -> Optional[str]:
    normalized = _normalized_text(text)
    for phrase, style in CANONICAL_STYLE_MAP.items():
        if re.search(rf"(?<!\w){re.escape(_clean_phrase(phrase))}(?!\w)", normalized):
            return style
    if isinstance(meta, dict):
        meta_style = meta.get("style")
        if meta_style:
            cleaned = _clean_phrase(meta_style)
            if cleaned in CANONICAL_STYLE_MAP:
                return CANONICAL_STYLE_MAP[cleaned]
            return cleaned
    return None


def _extract_location(text: str, meta: Optional[dict[str, Any]] = None) -> Optional[str]:
    normalized = _normalized_text(text)
    hits = _find_mapping_hits(normalized, LOCATION_SYNONYMS)
    if hits:
        return hits[0]
    if isinstance(meta, dict):
        meta_location = meta.get("location")
        if meta_location:
            cleaned = _clean_phrase(meta_location)
            if cleaned in CANONICAL_LOCATION_MAP:
                return CANONICAL_LOCATION_MAP[cleaned]
            return cleaned
    return None


def _extract_equipment(text: str, meta: Optional[dict[str, Any]] = None) -> list[str]:
    normalized = _normalized_text(text)
    hits = _find_mapping_hits(normalized, EQUIPMENT_SYNONYMS)
    if isinstance(meta, dict):
        meta_equipment = meta.get("equipment")
        if isinstance(meta_equipment, list):
            hits.extend(str(item) for item in meta_equipment)
        elif meta_equipment:
            hits.append(str(meta_equipment))
    normalized_hits: list[str] = []
    for hit in hits:
        cleaned = _clean_phrase(hit)
        if cleaned in CANONICAL_EQUIPMENT_MAP:
            normalized_hits.append(CANONICAL_EQUIPMENT_MAP[cleaned])
        else:
            normalized_hits.append(cleaned)
    return _unique_keep_order(normalized_hits)


def _extract_exclusions(text: str, meta: Optional[dict[str, Any]] = None) -> list[str]:
    normalized = _normalized_text(text)
    hits: list[str] = []
    for key, values in EXCLUSION_SYNONYMS.items():
        for value in values:
            cleaned = _clean_phrase(value)
            if re.search(rf"(?<!\w){re.escape(cleaned)}(?!\w)", normalized):
                hits.append(key)
                break
    if isinstance(meta, dict):
        meta_exclusions = meta.get("exclusions")
        if isinstance(meta_exclusions, list):
            hits.extend(str(item) for item in meta_exclusions)
        elif meta_exclusions:
            hits.append(str(meta_exclusions))
    return _unique_keep_order(hits)


def _extract_goals(text: str, meta: Optional[dict[str, Any]] = None) -> list[str]:
    normalized = _normalized_text(text)
    hits = _find_mapping_hits(normalized, GOAL_SYNONYMS)

    if isinstance(meta, dict):
        goals = meta.get("goals")
        if isinstance(goals, list):
            hits.extend(str(item) for item in goals)
        elif goals:
            hits.append(str(goals))

        single_goal = meta.get("goal")
        if single_goal:
            hits.append(str(single_goal))

    normalized_hits: list[str] = []
    for hit in hits:
        cleaned = _clean_phrase(hit)
        if cleaned in CANONICAL_GOAL_MAP:
            normalized_hits.append(CANONICAL_GOAL_MAP[cleaned])
        else:
            normalized_hits.append(cleaned.replace(" ", "_"))
    return _unique_keep_order(normalized_hits)


def _extract_body_focus(text: str, meta: Optional[dict[str, Any]] = None) -> list[str]:
    normalized = _normalized_text(text)
    hits = _find_mapping_hits(normalized, BODY_FOCUS_SYNONYMS)
    if isinstance(meta, dict):
        meta_focus = meta.get("body_focus")
        if isinstance(meta_focus, list):
            hits.extend(str(item) for item in meta_focus)
        elif meta_focus:
            hits.append(str(meta_focus))
    normalized_hits: list[str] = []
    for hit in hits:
        cleaned = _clean_phrase(hit)
        if cleaned in CANONICAL_BODY_FOCUS_MAP:
            normalized_hits.append(CANONICAL_BODY_FOCUS_MAP[cleaned])
        else:
            normalized_hits.append(cleaned)
    return _unique_keep_order(normalized_hits)


def _extract_workout_types(text: str, meta: Optional[dict[str, Any]] = None) -> list[str]:
    normalized = _normalized_text(text)
    hits = _find_mapping_hits(normalized, WORKOUT_TYPE_SYNONYMS)
    if isinstance(meta, dict):
        meta_types = meta.get("workout_types")
        if isinstance(meta_types, list):
            hits.extend(str(item) for item in meta_types)
        elif meta_types:
            hits.append(str(meta_types))
    normalized_hits: list[str] = []
    for hit in hits:
        cleaned = _clean_phrase(hit)
        if cleaned in CANONICAL_WORKOUT_TYPE_MAP:
            normalized_hits.append(CANONICAL_WORKOUT_TYPE_MAP[cleaned])
        else:
            normalized_hits.append(cleaned)
    return _unique_keep_order(normalized_hits)


def _extract_health_flags(text: str, meta: Optional[dict[str, Any]] = None) -> list[str]:
    normalized = _normalized_text(text)
    hits = _find_mapping_hits(normalized, HEALTH_FLAGS)
    if isinstance(meta, dict):
        meta_health = meta.get("health_flags")
        if isinstance(meta_health, list):
            hits.extend(str(item) for item in meta_health)
        elif meta_health:
            hits.append(str(meta_health))
    normalized_hits: list[str] = []
    for hit in hits:
        cleaned = _clean_phrase(hit)
        if cleaned in CANONICAL_HEALTH_MAP:
            normalized_hits.append(CANONICAL_HEALTH_MAP[cleaned])
        else:
            normalized_hits.append(cleaned)
    return _unique_keep_order(normalized_hits)


def _extract_age_group(text: str, meta: Optional[dict[str, Any]] = None) -> Optional[str]:
    normalized = _normalized_text(text)
    explicit_age = _extract_age_number(normalized)
    if explicit_age is not None:
        if explicit_age < 18:
            return "teen"
        if explicit_age >= 60:
            return "senior"
        return "adult"

    for phrase, age_group in CANONICAL_AGE_MAP.items():
        if re.search(rf"(?<!\w){re.escape(_clean_phrase(phrase))}(?!\w)", normalized):
            return age_group

    if isinstance(meta, dict):
        meta_age_group = meta.get("user_age_group")
        if meta_age_group:
            return _clean_phrase(meta_age_group)
    return None


def _extract_user_sex(text: str, meta: Optional[dict[str, Any]] = None) -> Optional[str]:
    normalized = _normalized_text(text)
    for phrase, sex in CANONICAL_SEX_MAP.items():
        if re.search(rf"(?<!\w){re.escape(_clean_phrase(phrase))}(?!\w)", normalized):
            return sex
    if isinstance(meta, dict):
        meta_user_sex = meta.get("user_sex")
        if meta_user_sex:
            return _clean_phrase(meta_user_sex)
    return None


def _extract_schedule_preferences(text: str, meta: Optional[dict[str, Any]] = None) -> tuple[list[str], list[str], list[str]]:
    normalized = _normalized_text(text)
    preferences = _find_mapping_hits(normalized, PREFERENCE_SYNONYMS)

    preferred_rest_days: list[str] = []
    preferred_workout_days: list[str] = []

    for day_name, variants in SCHEDULE_HINTS.items():
        for variant in variants:
            cleaned = _clean_phrase(variant)
            rest_patterns = [
                rf"rest\s+on\s+{re.escape(cleaned)}",
                rf"{re.escape(cleaned)}\s+rest",
                rf"выходной\s+в\s+{re.escape(cleaned)}",
                rf"отдых\s+в\s+{re.escape(cleaned)}",
            ]
            workout_patterns = [
                rf"train\s+on\s+{re.escape(cleaned)}",
                rf"workout\s+on\s+{re.escape(cleaned)}",
                rf"тренировка\s+в\s+{re.escape(cleaned)}",
                rf"занятие\s+в\s+{re.escape(cleaned)}",
            ]
            for pattern in rest_patterns:
                if re.search(pattern, normalized):
                    preferred_rest_days.append(day_name)
                    break
            for pattern in workout_patterns:
                if re.search(pattern, normalized):
                    preferred_workout_days.append(day_name)
                    break

    if isinstance(meta, dict):
        meta_preferences = meta.get("schedule_preferences")
        if isinstance(meta_preferences, list):
            preferences.extend(str(item) for item in meta_preferences)
        elif meta_preferences:
            preferences.append(str(meta_preferences))

        meta_rest_days = meta.get("preferred_rest_days")
        if isinstance(meta_rest_days, list):
            preferred_rest_days.extend(str(item) for item in meta_rest_days)

        meta_workout_days = meta.get("preferred_workout_days")
        if isinstance(meta_workout_days, list):
            preferred_workout_days.extend(str(item) for item in meta_workout_days)

    return (
        _unique_keep_order(preferences),
        _unique_keep_order(preferred_rest_days),
        _unique_keep_order(preferred_workout_days),
    )


def _extract_notes(text: str, meta: Optional[dict[str, Any]] = None) -> str:
    note_parts: list[str] = []
    normalized = _normalized_text(text)

    if _contains_any(normalized, STRICTNESS_MARKERS):
        note_parts.append("user requested strict interpretation")

    if _contains_any(normalized, RECOVERY_MARKERS) and not _contains_any(normalized, REST_MARKERS):
        note_parts.append("recovery markers present without explicit rest markers")

    if isinstance(meta, dict):
        raw_notes = meta.get("notes")
        if raw_notes:
            note_parts.append(str(raw_notes).strip())

    return "; ".join(part for part in note_parts if str(part).strip())


def _merge_meta_days(
    total_days: int,
    rest_days: Optional[int],
    strict: bool,
    meta: Optional[dict[str, Any]],
) -> tuple[int, Optional[int], bool]:
    metadata = dict(meta or {})

    meta_total_days = _safe_int(metadata.get("total_days"))
    if meta_total_days is not None:
        total_days = meta_total_days

    meta_rest_days = _safe_int(metadata.get("rest_days"))
    if meta_rest_days is not None:
        rest_days = meta_rest_days
        strict = bool(metadata.get("rest_days_strict", True))

    meta_workouts_per_week = _safe_int(metadata.get("workouts_per_week"))
    if meta_workouts_per_week is not None and rest_days is None and total_days == 7:
        rest_days = max(0, total_days - meta_workouts_per_week)
        strict = True

    return total_days, rest_days, strict


def _needs_clarification(
    task_type: str,
    total_days: int,
    rest_days: Optional[int],
    goals: list[str],
    workout_types: list[str],
) -> bool:
    if not task_type:
        return True
    if total_days <= 0:
        return True
    if not goals and not workout_types and task_type in {"workout_plan", "weekly_plan", "monthly_plan", "plan_generation"}:
        return False
    if rest_days is not None and rest_days > total_days:
        return True
    return False


def _build_issues(
    *,
    total_days: int,
    rest_days: Optional[int],
    strict: bool,
    text: str,
    workout_types: list[str],
    goals: list[str],
    health_flags: list[str],
) -> list[ParseIssue]:
    issues: list[ParseIssue] = []

    if rest_days is not None and total_days == rest_days:
        issues.append(ParseIssue(code="rest_equals_total", message="rest days equal total days; parser may need weekly fallback", severity="info"))

    if _contains_any(text, RECOVERY_MARKERS) and strict and not _contains_any(text, REST_MARKERS):
        issues.append(ParseIssue(code="recovery_vs_rest", message="recovery words were present but strict rest was requested", severity="info"))

    if "hiit" in workout_types and "low_impact" in _find_mapping_hits(text, PREFERENCE_SYNONYMS):
        issues.append(ParseIssue(code="possible_intensity_conflict", message="HIIT requested together with low impact preference", severity="warning"))

    if "rehab" in goals and not health_flags:
        issues.append(ParseIssue(code="rehab_without_condition", message="rehab goal detected without explicit health condition", severity="info"))

    if total_days > 90:
        issues.append(ParseIssue(code="too_many_days", message="total_days is very large", severity="warning"))

    return issues


def parse_plan_request(prompt_text: str, meta: Optional[dict[str, Any]] = None) -> PlanRequestUnderstanding:
    text = str(prompt_text or "").strip()
    metadata = dict(meta or {})
    normalized = _normalized_text(text)

    weekly = _detect_weekly(normalized)
    task_type = _extract_task_type(normalized)

    total_days = _extract_duration_days(normalized, weekly_default=weekly)
    rest_days, strict = _extract_rest_days(normalized)

    if weekly and strict and rest_days is not None and total_days == rest_days:
        total_days = 7

    total_days, rest_days, strict = _merge_meta_days(total_days, rest_days, strict, metadata)

    goals = _extract_goals(normalized, metadata)
    workouts_per_week = _extract_workouts_per_week(normalized, total_days, rest_days)
    duration_weeks = _extract_duration_weeks(normalized, total_days)
    duration_months = _extract_duration_months(normalized, total_days)
    time_per_session_minutes = _extract_time_per_session_minutes(normalized)
    level = _extract_level(normalized, metadata)
    intensity = _extract_intensity(normalized, metadata)
    style = _extract_style(normalized, metadata)
    location = _extract_location(normalized, metadata)
    equipment = _extract_equipment(normalized, metadata)
    exclusions = _extract_exclusions(normalized, metadata)
    body_focus = _extract_body_focus(normalized, metadata)
    workout_types = _extract_workout_types(normalized, metadata)
    health_flags = _extract_health_flags(normalized, metadata)
    user_sex = _extract_user_sex(normalized, metadata)
    user_age_group = _extract_age_group(normalized, metadata)
    schedule_preferences, preferred_rest_days, preferred_workout_days = _extract_schedule_preferences(normalized, metadata)
    notes = _extract_notes(normalized, metadata)

    understanding = PlanRequestUnderstanding(
        task_type=task_type,
        total_days=total_days,
        rest_days=rest_days,
        rest_days_strict=bool(strict),
        inferred_goals=goals,
        workouts_per_week=workouts_per_week,
        duration_weeks=duration_weeks,
        duration_months=duration_months,
        time_per_session_minutes=time_per_session_minutes,
        level=level,
        intensity=intensity,
        style=style,
        location=location,
        equipment=equipment,
        exclusions=exclusions,
        body_focus=body_focus,
        workout_types=workout_types,
        health_flags=health_flags,
        user_sex=user_sex,
        user_age_group=user_age_group,
        schedule_preferences=schedule_preferences,
        preferred_rest_days=preferred_rest_days,
        preferred_workout_days=preferred_workout_days,
        notes=notes,
    )

    understanding.needs_clarification = _needs_clarification(
        task_type=understanding.task_type,
        total_days=understanding.total_days,
        rest_days=understanding.rest_days,
        goals=understanding.inferred_goals,
        workout_types=understanding.workout_types,
    )
    understanding.issues = _build_issues(
        total_days=understanding.total_days,
        rest_days=understanding.rest_days,
        strict=understanding.rest_days_strict,
        text=normalized,
        workout_types=understanding.workout_types,
        goals=understanding.inferred_goals,
        health_flags=understanding.health_flags,
    )

    validate_plan_request(understanding)
    return understanding


def validate_plan_request(understanding: PlanRequestUnderstanding) -> None:
    if understanding.total_days <= 0:
        raise ValueError("total_days must be > 0")
    if understanding.total_days > 365:
        raise ValueError("total_days must be <= 365")

    if understanding.rest_days is None:
        return
    if understanding.rest_days < 0:
        raise ValueError("rest_days must be >= 0")
    if understanding.rest_days >= understanding.total_days:
        raise ValueError("rest_days must be less than total_days")
    if understanding.active_days != (understanding.total_days - understanding.rest_days):
        raise ValueError("active_days must equal total_days - rest_days")


def apply_understanding_to_meta(meta: Optional[dict[str, Any]], understanding: PlanRequestUnderstanding) -> dict[str, Any]:
    out = dict(meta or {})
    out["task_type"] = understanding.task_type
    out["total_days"] = int(understanding.total_days)
    out["rest_days"] = int(understanding.rest_days) if understanding.rest_days is not None else None
    out["rest_days_strict"] = bool(understanding.rest_days_strict)
    out["active_days"] = int(understanding.active_days)
    out["workouts_per_week"] = int(understanding.workouts_per_week) if understanding.workouts_per_week is not None else (
        int(understanding.total_days - understanding.rest_days) if understanding.rest_days is not None else None
    )
    out["duration_weeks"] = understanding.duration_weeks
    out["duration_months"] = understanding.duration_months
    out["time_per_session_minutes"] = understanding.time_per_session_minutes
    out["level"] = understanding.level
    out["intensity"] = understanding.intensity
    out["style"] = understanding.style
    out["location"] = understanding.location
    out["equipment"] = list(understanding.equipment)
    out["exclusions"] = list(understanding.exclusions)
    out["body_focus"] = list(understanding.body_focus)
    out["workout_types"] = list(understanding.workout_types)
    out["health_flags"] = list(understanding.health_flags)
    out["user_sex"] = understanding.user_sex
    out["user_age_group"] = understanding.user_age_group
    out["schedule_preferences"] = list(understanding.schedule_preferences)
    out["preferred_rest_days"] = list(understanding.preferred_rest_days)
    out["preferred_workout_days"] = list(understanding.preferred_workout_days)
    out["notes"] = understanding.notes
    out["needs_clarification"] = bool(understanding.needs_clarification)
    out["issues"] = [
        {"code": issue.code, "message": issue.message, "severity": issue.severity}
        for issue in understanding.issues
    ]

    if understanding.inferred_goals:
        existing = out.get("goals")
        goals = existing if isinstance(existing, list) else ([existing] if existing else [])
        normalized = [str(g).strip() for g in goals if str(g).strip()]
        for goal in understanding.inferred_goals:
            if goal not in normalized:
                normalized.append(goal)
        out["goals"] = normalized

    if understanding.rest_days is not None:
        out["days_per_week"] = int(understanding.total_days - understanding.rest_days)

    return out


def validate_plan_distribution(
    days: list[dict[str, Any]],
    *,
    total_days: int,
    rest_days: Optional[int],
    strict: bool,
) -> bool:
    if not isinstance(days, list):
        return False
    if len(days) != int(total_days):
        return False
    if rest_days is None or not strict:
        return True

    rest_count = 0
    for item in days:
        day_type = str((item or {}).get("type", "")).strip().lower()
        if day_type in {"workout", "training", "active"}:
            continue
        rest_count += 1
    return rest_count == int(rest_days)


def understanding_to_dict(understanding: PlanRequestUnderstanding) -> dict[str, Any]:
    return apply_understanding_to_meta({}, understanding)


def parse_plan_request_to_dict(prompt_text: str, meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    return understanding_to_dict(parse_plan_request(prompt_text, meta))


def summarize_understanding(understanding: PlanRequestUnderstanding) -> str:
    parts: list[str] = [
        f"task_type={understanding.task_type}",
        f"total_days={understanding.total_days}",
        f"rest_days={understanding.rest_days}",
        f"strict={understanding.rest_days_strict}",
        f"goals={','.join(understanding.inferred_goals) if understanding.inferred_goals else '-'}",
    ]
    if understanding.workouts_per_week is not None:
        parts.append(f"workouts_per_week={understanding.workouts_per_week}")
    if understanding.time_per_session_minutes is not None:
        parts.append(f"time_per_session_minutes={understanding.time_per_session_minutes}")
    if understanding.level:
        parts.append(f"level={understanding.level}")
    if understanding.intensity:
        parts.append(f"intensity={understanding.intensity}")
    if understanding.style:
        parts.append(f"style={understanding.style}")
    if understanding.location:
        parts.append(f"location={understanding.location}")
    return " | ".join(parts)



# ============================================================
# Extended validation / normalization layer
# ============================================================


@dataclass
class ValidationDetail:
    code: str
    message: str
    severity: str = "error"
    field: Optional[str] = None
    current_value: Any = None
    suggested_value: Any = None


@dataclass
class ValidationReport:
    ok: bool
    normalized: PlanRequestUnderstanding
    errors: list[ValidationDetail] = field(default_factory=list)
    warnings: list[ValidationDetail] = field(default_factory=list)

    def raise_for_errors(self) -> None:
        if not self.errors:
            return
        joined = "; ".join(f"{item.code}: {item.message}" for item in self.errors)
        raise ValueError(joined)


WEEKDAY_ALIASES: dict[str, list[str]] = {
    "monday": [
        "monday",
        "mon",
        "понедельник",
        "понедельника",
        "пн",
        "pn",
        "ponedelnik",
    ],
    "tuesday": [
        "tuesday",
        "tue",
        "tues",
        "вторник",
        "вторника",
        "вт",
        "vt",
        "vtornik",
    ],
    "wednesday": [
        "wednesday",
        "wed",
        "среда",
        "среду",
        "среды",
        "ср",
        "sr",
        "sreda",
    ],
    "thursday": [
        "thursday",
        "thu",
        "thur",
        "четверг",
        "четверга",
        "чт",
        "cht",
        "chetverg",
    ],
    "friday": [
        "friday",
        "fri",
        "пятница",
        "пятницу",
        "пятницы",
        "пт",
        "pt",
        "pyatnica",
    ],
    "saturday": [
        "saturday",
        "sat",
        "суббота",
        "субботу",
        "субботы",
        "сб",
        "sb",
        "subbota",
    ],
    "sunday": [
        "sunday",
        "sun",
        "воскресенье",
        "воскресенья",
        "вс",
        "vs",
        "voskresene",
        "voskresенье",
    ],
}


GOAL_COMPATIBILITY_RULES: dict[str, dict[str, list[str]]] = {
    "lose_weight": {
        "preferred_types": [
            "cardio",
            "hiit",
            "walking",
            "cycling",
            "full_body",
            "functional",
        ],
        "discouraged_styles": [
            "powerlifting",
        ],
        "preferred_intensity": [
            "low",
            "medium",
            "high",
        ],
    },
    "gain_muscle": {
        "preferred_types": [
            "strength",
            "upper_body",
            "lower_body",
            "push_pull_legs",
            "hypertrophy",
        ],
        "discouraged_styles": [
            "endurance_only",
        ],
        "preferred_intensity": [
            "medium",
            "high",
        ],
    },
    "build_strength": {
        "preferred_types": [
            "strength",
            "powerlifting",
            "upper_body",
            "lower_body",
        ],
        "discouraged_styles": [
            "cardio_only",
        ],
        "preferred_intensity": [
            "medium",
            "high",
        ],
    },
    "endurance": {
        "preferred_types": [
            "cardio",
            "running",
            "cycling",
            "swimming",
            "walking",
        ],
        "discouraged_styles": [
            "strength_only",
        ],
        "preferred_intensity": [
            "low",
            "medium",
            "high",
        ],
    },
    "mobility": {
        "preferred_types": [
            "mobility",
            "stretching",
            "yoga",
            "pilates",
        ],
        "discouraged_styles": [
            "powerlifting",
            "max_intensity_only",
        ],
        "preferred_intensity": [
            "low",
            "medium",
        ],
    },
    "general_fitness": {
        "preferred_types": [
            "full_body",
            "cardio",
            "strength",
            "walking",
            "mobility",
        ],
        "discouraged_styles": [],
        "preferred_intensity": [
            "low",
            "medium",
            "high",
        ],
    },
}


HEALTH_CONTRA_RULES: dict[str, dict[str, list[str]]] = {
    "back_pain": {
        "discouraged_body_focus": [
            "lower_back",
        ],
        "discouraged_workout_types": [
            "high_impact",
            "powerlifting",
            "plyometrics",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "knee_pain": {
        "discouraged_body_focus": [
            "quads",
            "glutes",
            "hamstrings",
        ],
        "discouraged_workout_types": [
            "running",
            "jumping",
            "plyometrics",
            "high_impact",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "shoulder_pain": {
        "discouraged_body_focus": [
            "shoulders",
            "chest",
        ],
        "discouraged_workout_types": [
            "overhead_pressing",
            "powerlifting",
            "plyometrics",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "neck_pain": {
        "discouraged_body_focus": [
            "shoulders",
            "upper_back",
        ],
        "discouraged_workout_types": [
            "heavy_strength",
            "powerlifting",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "obesity": {
        "discouraged_workout_types": [
            "high_impact",
            "jumping",
            "sprinting",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "pregnancy": {
        "discouraged_workout_types": [
            "max_hr",
            "combat",
            "high_impact",
            "heavy_powerlifting",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "postpartum": {
        "discouraged_workout_types": [
            "high_impact",
            "heavy_powerlifting",
            "max_hr",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "hypertension": {
        "discouraged_workout_types": [
            "max_hr",
            "all_out_hiit",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "heart_condition": {
        "discouraged_workout_types": [
            "max_hr",
            "all_out_hiit",
            "sprinting",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
    "diabetes": {
        "discouraged_workout_types": [],
        "discouraged_intensity": [],
    },
    "joint_pain": {
        "discouraged_workout_types": [
            "high_impact",
            "jumping",
            "sprinting",
        ],
        "discouraged_intensity": [
            "high",
        ],
    },
}


LEVEL_ALLOWED_INTENSITY: dict[str, list[str]] = {
    "beginner": [
        "low",
        "medium",
    ],
    "intermediate": [
        "low",
        "medium",
        "high",
    ],
    "advanced": [
        "medium",
        "high",
    ],
}


LEVEL_MAX_WORKOUTS: dict[str, int] = {
    "beginner": 5,
    "intermediate": 6,
    "advanced": 7,
}


LEVEL_MAX_SESSION_MINUTES: dict[str, int] = {
    "beginner": 90,
    "intermediate": 120,
    "advanced": 180,
}


LOCATION_EQUIPMENT_COMPATIBILITY: dict[str, dict[str, list[str]]] = {
    "home": {
        "discouraged_equipment": [
            "barbell",
            "smith_machine",
            "leg_press",
            "cable_machine",
            "rowing_machine",
            "treadmill",
            "lat_pulldown",
        ],
        "preferred_equipment": [
            "bodyweight",
            "dumbbells",
            "bands",
            "kettlebell",
            "mat",
        ],
    },
    "gym": {
        "discouraged_equipment": [],
        "preferred_equipment": [
            "barbell",
            "dumbbells",
            "machine",
            "cable_machine",
            "treadmill",
            "bike",
            "elliptical",
        ],
    },
    "outdoor": {
        "discouraged_equipment": [
            "smith_machine",
            "leg_press",
            "cable_machine",
            "bench_press_station",
        ],
        "preferred_equipment": [
            "bodyweight",
            "bands",
            "running",
            "walking",
            "pullup_bar",
        ],
    },
}


WORKOUT_TYPE_IMPLICIT_LOAD: dict[str, str] = {
    "walking": "low",
    "mobility": "low",
    "stretching": "low",
    "yoga": "low",
    "pilates": "low",
    "cycling": "medium",
    "cardio": "medium",
    "running": "medium",
    "swimming": "medium",
    "functional": "medium",
    "strength": "medium",
    "upper_body": "medium",
    "lower_body": "medium",
    "full_body": "medium",
    "hiit": "high",
    "powerlifting": "high",
    "plyometrics": "high",
    "sprinting": "high",
    "combat": "high",
}


SESSION_TIME_HINTS: dict[str, tuple[int, int]] = {
    "walking": (15, 120),
    "mobility": (10, 60),
    "stretching": (10, 60),
    "yoga": (15, 90),
    "pilates": (15, 90),
    "cycling": (20, 120),
    "cardio": (15, 120),
    "running": (15, 120),
    "swimming": (20, 120),
    "functional": (20, 90),
    "strength": (20, 120),
    "upper_body": (20, 120),
    "lower_body": (20, 120),
    "full_body": (20, 120),
    "hiit": (10, 45),
    "powerlifting": (30, 150),
    "plyometrics": (10, 45),
    "sprinting": (10, 45),
    "combat": (20, 90),
}


DEFAULT_DURATION_DAYS_BY_TASK: dict[str, int] = {
    "weekly_plan": 7,
    "monthly_plan": 30,
    "plan_generation": 30,
}


NORMALIZATION_SYNONYMS: dict[str, dict[str, list[str]]] = {
    "level": {
        "beginner": [
            "beginner",
            "newbie",
            "novice",
            "starter",
            "начинающий",
            "начинающая",
            "novichok",
            "s nulya",
            "с нуля",
        ],
        "intermediate": [
            "intermediate",
            "medium",
            "средний",
            "sredniy",
            "middle",
        ],
        "advanced": [
            "advanced",
            "pro",
            "experienced",
            "опытный",
            "prodvinutiy",
            "продвинутый",
        ],
    },
    "intensity": {
        "low": [
            "low",
            "easy",
            "light",
            "soft",
            "low intensity",
            "низкая",
            "легкая",
            "spokoyno",
            "bez pereza",
        ],
        "medium": [
            "medium",
            "moderate",
            "normal",
            "average",
            "средняя",
            "умеренная",
            "umerennaya",
        ],
        "high": [
            "high",
            "hard",
            "intense",
            "aggressive",
            "high intensity",
            "высокая",
            "интенсивная",
            "slozhno",
            "jostko",
        ],
    },
    "location": {
        "home": [
            "home",
            "at home",
            "house",
            "dom",
            "дома",
            "дом",
            "домашний",
        ],
        "gym": [
            "gym",
            "fitness club",
            "club",
            "zal",
            "зал",
            "в зале",
        ],
        "outdoor": [
            "outdoor",
            "outside",
            "street",
            "park",
            "улица",
            "на улице",
            "park",
        ],
    },
    "style": {
        "strict": [
            "strict",
            "structured",
            "discipline",
            "строго",
            "четко",
            "по дням",
        ],
        "flexible": [
            "flexible",
            "adaptive",
            "soft",
            "гибко",
            "адаптивно",
        ],
        "minimal": [
            "minimal",
            "simple",
            "basic",
            "минимальный",
            "простой",
        ],
        "athletic": [
            "athletic",
            "sport",
            "sporty",
            "спортивный",
            "athlete",
        ],
    },
}


STRICT_PHRASE_PATTERNS: dict[str, list[str]] = {
    "exactly": [
        r"\bexactly\b",
        r"\bstrict(?:ly)?\b",
        r"\bnot more and not less\b",
        r"\bровно\b",
        r"\bстрого\b",
        r"\bexact\b",
    ],
    "avoid_assumptions": [
        r"\bdo not add assumptions\b",
        r"\bdont add assumptions\b",
        r"\bбез допущений\b",
        r"\bбез догадок\b",
        r"\bfollow exactly\b",
    ],
    "recovery_not_rest": [
        r"\brecovery day is not rest day\b",
        r"\brecovery != rest\b",
        r"\bвосстановление не отдых\b",
    ],
}


SOFT_REST_DAY_LABELS: set[str] = {
    "recovery",
    "light",
    "mobility",
    "stretching",
    "walk",
    "walking",
    "active_recovery",
    "active recovery",
    "rehab",
    "cardio_light",
}


HARD_REST_DAY_LABELS: set[str] = {
    "rest",
    "off",
    "rest_day",
    "rest day",
    "day off",
    "full_rest",
    "full rest",
    "complete_rest",
    "complete rest",
}


DISTRIBUTION_ACTIVITY_TOKENS: dict[str, list[str]] = {
    "rest": [
        "rest",
        "off",
        "rest day",
        "day off",
        "full rest",
        "complete rest",
        "выходной",
        "отдых",
    ],
    "recovery": [
        "recovery",
        "active recovery",
        "mobility",
        "stretching",
        "yoga",
        "light walk",
        "walk",
        "rehab",
        "восстановление",
        "растяжка",
        "мобилити",
    ],
    "workout": [
        "workout",
        "training",
        "session",
        "cardio",
        "strength",
        "run",
        "running",
        "upper body",
        "lower body",
        "full body",
        "hiit",
        "weights",
        "exercise",
        "тренировка",
        "кардио",
        "силовая",
        "бег",
        "ходьба",
    ],
}


def _contains_pattern(text: str, patterns: list[str]) -> bool:
    lower = _normalized_text(text)
    for pattern in patterns:
        if re.search(pattern, lower):
            return True
    return False


def _append_detail(
    bucket: list[ValidationDetail],
    *,
    code: str,
    message: str,
    severity: str = "error",
    field: Optional[str] = None,
    current_value: Any = None,
    suggested_value: Any = None,
) -> None:
    bucket.append(
        ValidationDetail(
            code=code,
            message=message,
            severity=severity,
            field=field,
            current_value=current_value,
            suggested_value=suggested_value,
        )
    )


def _clone_understanding(understanding: PlanRequestUnderstanding) -> PlanRequestUnderstanding:
    return PlanRequestUnderstanding(
        task_type=understanding.task_type,
        total_days=int(understanding.total_days),
        rest_days=None if understanding.rest_days is None else int(understanding.rest_days),
        rest_days_strict=bool(understanding.rest_days_strict),
        inferred_goals=list(understanding.inferred_goals),
        workouts_per_week=understanding.workouts_per_week,
        duration_weeks=understanding.duration_weeks,
        duration_months=understanding.duration_months,
        time_per_session_minutes=understanding.time_per_session_minutes,
        level=understanding.level,
        intensity=understanding.intensity,
        style=understanding.style,
        location=understanding.location,
        equipment=list(understanding.equipment),
        exclusions=list(understanding.exclusions),
        body_focus=list(understanding.body_focus),
        workout_types=list(understanding.workout_types),
        health_flags=list(understanding.health_flags),
        user_sex=understanding.user_sex,
        user_age_group=understanding.user_age_group,
        schedule_preferences=list(understanding.schedule_preferences),
        preferred_rest_days=list(understanding.preferred_rest_days),
        preferred_workout_days=list(understanding.preferred_workout_days),
        notes=str(understanding.notes),
        needs_clarification=bool(understanding.needs_clarification),
        issues=list(understanding.issues),
    )


def _infer_workouts_per_week_from_days(
    total_days: int,
    rest_days: Optional[int],
) -> Optional[int]:
    if total_days == 7 and rest_days is not None:
        return max(0, total_days - rest_days)
    return None


def _canonicalize_from_mapping(value: Optional[str], mapping_name: str) -> Optional[str]:
    if not value:
        return value
    lower = _normalized_text(value)
    mapping = NORMALIZATION_SYNONYMS.get(mapping_name, {})
    for canonical, aliases in mapping.items():
        values = [_normalized_text(canonical)] + [_normalized_text(item) for item in aliases]
        if lower in values:
            return canonical
    return value


def _canonicalize_schedule_days(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        lower = _normalized_text(value)
        matched = None
        for day, aliases in WEEKDAY_ALIASES.items():
            if lower == day or lower in [_normalized_text(item) for item in aliases]:
                matched = day
                break
        out.append(matched or lower)
    return _unique_keep_order([item for item in out if item])


def _canonicalize_goal(goal: str) -> str:
    value = _normalized_text(goal).replace(" ", "_")
    replacements = {
        "weight_loss": "lose_weight",
        "fat_loss": "lose_weight",
        "lose_fat": "lose_weight",
        "muscle_gain": "gain_muscle",
        "gain_mass": "gain_muscle",
        "build_muscle": "gain_muscle",
        "strength": "build_strength",
        "stamina": "endurance",
        "mobility_flexibility": "mobility",
        "fitness": "general_fitness",
    }
    return replacements.get(value, value)


def _canonicalize_workout_type(value: str) -> str:
    lower = _normalized_text(value).replace("-", "_").replace(" ", "_")
    replacements = {
        "weights": "strength",
        "lifting": "strength",
        "weight_training": "strength",
        "jogging": "running",
        "walk": "walking",
        "stretch": "stretching",
        "mobility_work": "mobility",
        "fullbody": "full_body",
        "upperbody": "upper_body",
        "lowerbody": "lower_body",
        "ppl": "push_pull_legs",
        "sprints": "sprinting",
    }
    return replacements.get(lower, lower)


def _canonicalize_equipment(value: str) -> str:
    lower = _normalized_text(value).replace("-", "_").replace(" ", "_")
    replacements = {
        "db": "dumbbells",
        "dumbbell": "dumbbells",
        "kettlebells": "kettlebell",
        "rubber_bands": "bands",
        "elastic_bands": "bands",
        "trx": "suspension_trainer",
        "matress": "mat",
        "pull_up_bar": "pullup_bar",
        "bar": "barbell",
    }
    return replacements.get(lower, lower)


def _canonicalize_body_focus(value: str) -> str:
    lower = _normalized_text(value).replace("-", "_").replace(" ", "_")
    replacements = {
        "arms": "biceps_triceps",
        "core": "abs_core",
        "abs": "abs_core",
        "glute": "glutes",
        "legs": "quads_hamstrings_glutes",
        "back": "upper_back_lower_back",
    }
    return replacements.get(lower, lower)


def _canonicalize_health_flag(value: str) -> str:
    lower = _normalized_text(value).replace("-", "_").replace(" ", "_")
    replacements = {
        "high_blood_pressure": "hypertension",
        "bp": "hypertension",
        "heart_problem": "heart_condition",
        "bad_knees": "knee_pain",
        "bad_back": "back_pain",
        "shoulder_issue": "shoulder_pain",
        "joint_issues": "joint_pain",
        "after_pregnancy": "postpartum",
    }
    return replacements.get(lower, lower)


def _normalize_understanding_values(understanding: PlanRequestUnderstanding) -> PlanRequestUnderstanding:
    u = _clone_understanding(understanding)

    u.task_type = _normalized_text(u.task_type).replace(" ", "_") or "plan_generation"
    u.level = _canonicalize_from_mapping(u.level, "level")
    u.intensity = _canonicalize_from_mapping(u.intensity, "intensity")
    u.location = _canonicalize_from_mapping(u.location, "location")
    u.style = _canonicalize_from_mapping(u.style, "style")

    u.inferred_goals = _unique_keep_order([_canonicalize_goal(item) for item in u.inferred_goals if item])
    u.equipment = _unique_keep_order([_canonicalize_equipment(item) for item in u.equipment if item])
    u.exclusions = _unique_keep_order([_normalized_text(item).replace(" ", "_") for item in u.exclusions if item])
    u.body_focus = _unique_keep_order([_canonicalize_body_focus(item) for item in u.body_focus if item])
    u.workout_types = _unique_keep_order([_canonicalize_workout_type(item) for item in u.workout_types if item])
    u.health_flags = _unique_keep_order([_canonicalize_health_flag(item) for item in u.health_flags if item])
    u.schedule_preferences = _unique_keep_order([_normalized_text(item).replace(" ", "_") for item in u.schedule_preferences if item])
    u.preferred_rest_days = _canonicalize_schedule_days(u.preferred_rest_days)
    u.preferred_workout_days = _canonicalize_schedule_days(u.preferred_workout_days)

    if u.rest_days is None and u.workouts_per_week is not None and u.total_days == 7:
        inferred_rest = max(0, 7 - int(u.workouts_per_week))
        u.rest_days = inferred_rest

    if u.rest_days is not None:
        u.active_days = int(u.total_days) - int(u.rest_days)
    else:
        u.active_days = int(u.total_days)

    return u


def _validate_basic_ranges(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    if u.total_days <= 0:
        _append_detail(errors, code="invalid_total_days", message="total_days must be > 0", field="total_days", current_value=u.total_days, suggested_value=7)
    if u.total_days > 365:
        _append_detail(errors, code="too_many_total_days", message="total_days must be <= 365", field="total_days", current_value=u.total_days, suggested_value=365)

    if u.rest_days is not None and u.rest_days < 0:
        _append_detail(errors, code="negative_rest_days", message="rest_days must be >= 0", field="rest_days", current_value=u.rest_days, suggested_value=0)

    if u.rest_days is not None and u.total_days > 0 and u.rest_days >= u.total_days:
        _append_detail(errors, code="rest_days_exceed_total_days", message="rest_days must be less than total_days", field="rest_days", current_value=u.rest_days, suggested_value=max(0, u.total_days - 1))

    if u.workouts_per_week is not None:
        if u.workouts_per_week < 0:
            _append_detail(errors, code="negative_workouts_per_week", message="workouts_per_week must be >= 0", field="workouts_per_week", current_value=u.workouts_per_week, suggested_value=0)
        if u.workouts_per_week > 14:
            _append_detail(errors, code="too_many_workouts_per_week", message="workouts_per_week looks unrealistic", field="workouts_per_week", current_value=u.workouts_per_week, suggested_value=7)
        elif u.workouts_per_week > 7:
            _append_detail(warnings, code="double_sessions_detected", message="workouts_per_week > 7 implies multiple sessions per day", severity="warning", field="workouts_per_week", current_value=u.workouts_per_week)

    if u.time_per_session_minutes is not None:
        if u.time_per_session_minutes <= 0:
            _append_detail(errors, code="invalid_session_time", message="time_per_session_minutes must be > 0", field="time_per_session_minutes", current_value=u.time_per_session_minutes, suggested_value=30)
        if u.time_per_session_minutes > 360:
            _append_detail(errors, code="session_time_too_large", message="time_per_session_minutes must be <= 360", field="time_per_session_minutes", current_value=u.time_per_session_minutes, suggested_value=120)
        elif u.time_per_session_minutes > 180:
            _append_detail(warnings, code="very_long_sessions", message="Session length is unusually long", severity="warning", field="time_per_session_minutes", current_value=u.time_per_session_minutes)


def _validate_task_duration_consistency(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    if u.task_type == "weekly_plan" and u.total_days not in {7, 14, 21, 28}:
        _append_detail(
            warnings,
            code="weekly_task_non_week_multiple",
            message="weekly_plan usually uses a 7-day multiple",
            severity="warning",
            field="total_days",
            current_value=u.total_days,
            suggested_value=7,
        )
    if u.task_type == "monthly_plan" and u.total_days < 28:
        _append_detail(
            warnings,
            code="monthly_task_short_duration",
            message="monthly_plan usually has at least 28 days",
            severity="warning",
            field="total_days",
            current_value=u.total_days,
            suggested_value=30,
        )

    if u.duration_weeks is not None:
        expected_days = int(u.duration_weeks) * 7
        if abs(expected_days - int(u.total_days)) > 2:
            _append_detail(
                warnings,
                code="duration_weeks_mismatch",
                message="duration_weeks does not closely match total_days",
                severity="warning",
                field="duration_weeks",
                current_value=u.duration_weeks,
                suggested_value=max(1, round(u.total_days / 7)),
            )

    if u.duration_months is not None:
        expected_days = int(u.duration_months) * 30
        if abs(expected_days - int(u.total_days)) > 10:
            _append_detail(
                warnings,
                code="duration_months_mismatch",
                message="duration_months does not closely match total_days",
                severity="warning",
                field="duration_months",
                current_value=u.duration_months,
                suggested_value=max(1, round(u.total_days / 30)),
            )


def _validate_rest_frequency_consistency(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    if u.rest_days is not None:
        expected_active_days = int(u.total_days) - int(u.rest_days)
        if u.active_days != expected_active_days:
            _append_detail(
                errors,
                code="active_days_mismatch",
                message="active_days must equal total_days - rest_days",
                field="active_days",
                current_value=u.active_days,
                suggested_value=expected_active_days,
            )

        if u.total_days == 7 and u.workouts_per_week is not None and int(u.workouts_per_week) != expected_active_days:
            _append_detail(
                errors,
                code="weekly_frequency_conflict",
                message="For a 7-day plan, workouts_per_week conflicts with total_days - rest_days",
                field="workouts_per_week",
                current_value=u.workouts_per_week,
                suggested_value=expected_active_days,
            )

    inferred = _infer_workouts_per_week_from_days(u.total_days, u.rest_days)
    if u.workouts_per_week is None and inferred is not None:
        u.workouts_per_week = inferred

    if u.total_days == 7 and u.rest_days is None and u.workouts_per_week is not None:
        implied_rest = 7 - int(u.workouts_per_week)
        if implied_rest < 0:
            _append_detail(
                errors,
                code="workouts_per_week_exceed_week",
                message="For a 7-day plan, workouts_per_week cannot exceed 7 unless double sessions are explicitly supported",
                field="workouts_per_week",
                current_value=u.workouts_per_week,
                suggested_value=7,
            )
        elif implied_rest > 3:
            _append_detail(
                warnings,
                code="many_rest_days_inferred",
                message="High number of inferred rest days",
                severity="warning",
                field="workouts_per_week",
                current_value=u.workouts_per_week,
            )

    if u.rest_days_strict and u.rest_days is None:
        _append_detail(
            warnings,
            code="strict_rest_without_value",
            message="rest_days_strict is true but rest_days is not set",
            severity="warning",
            field="rest_days_strict",
            current_value=u.rest_days_strict,
            suggested_value=False,
        )


def _validate_level_intensity_consistency(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    if u.level and u.intensity:
        allowed = LEVEL_ALLOWED_INTENSITY.get(u.level)
        if allowed and u.intensity not in allowed:
            _append_detail(
                warnings,
                code="level_intensity_conflict",
                message=f"Intensity '{u.intensity}' is unusual for level '{u.level}'",
                severity="warning",
                field="intensity",
                current_value=u.intensity,
                suggested_value=allowed[0],
            )

    if u.level and u.workouts_per_week is not None:
        max_value = LEVEL_MAX_WORKOUTS.get(u.level)
        if max_value is not None and int(u.workouts_per_week) > int(max_value):
            _append_detail(
                warnings,
                code="level_frequency_too_high",
                message=f"workouts_per_week={u.workouts_per_week} is high for level '{u.level}'",
                severity="warning",
                field="workouts_per_week",
                current_value=u.workouts_per_week,
                suggested_value=max_value,
            )

    if u.level and u.time_per_session_minutes is not None:
        max_minutes = LEVEL_MAX_SESSION_MINUTES.get(u.level)
        if max_minutes is not None and int(u.time_per_session_minutes) > int(max_minutes):
            _append_detail(
                warnings,
                code="level_session_time_too_high",
                message=f"Session time is long for level '{u.level}'",
                severity="warning",
                field="time_per_session_minutes",
                current_value=u.time_per_session_minutes,
                suggested_value=max_minutes,
            )


def _validate_goal_type_consistency(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    if not u.inferred_goals:
        return

    for goal in u.inferred_goals:
        rule = GOAL_COMPATIBILITY_RULES.get(goal)
        if not rule:
            continue

        discouraged_styles = set(rule.get("discouraged_styles", []))
        if u.style and u.style in discouraged_styles:
            _append_detail(
                warnings,
                code="goal_style_conflict",
                message=f"Style '{u.style}' is unusual for goal '{goal}'",
                severity="warning",
                field="style",
                current_value=u.style,
            )

        preferred_intensity = set(rule.get("preferred_intensity", []))
        if u.intensity and preferred_intensity and u.intensity not in preferred_intensity:
            _append_detail(
                warnings,
                code="goal_intensity_conflict",
                message=f"Intensity '{u.intensity}' is unusual for goal '{goal}'",
                severity="warning",
                field="intensity",
                current_value=u.intensity,
            )

        preferred_types = set(rule.get("preferred_types", []))
        if u.workout_types and preferred_types:
            overlap = preferred_types.intersection(set(u.workout_types))
            if not overlap:
                _append_detail(
                    warnings,
                    code="goal_workout_type_mismatch",
                    message=f"Workout types do not clearly support goal '{goal}'",
                    severity="warning",
                    field="workout_types",
                    current_value=u.workout_types,
                    suggested_value=sorted(preferred_types),
                )


def _validate_health_constraints(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    for flag in u.health_flags:
        rule = HEALTH_CONTRA_RULES.get(flag)
        if not rule:
            continue

        discouraged_body_focus = set(rule.get("discouraged_body_focus", []))
        discouraged_workout_types = set(rule.get("discouraged_workout_types", []))
        discouraged_intensity = set(rule.get("discouraged_intensity", []))

        if u.body_focus and discouraged_body_focus.intersection(set(u.body_focus)):
            _append_detail(
                warnings,
                code="health_body_focus_conflict",
                message=f"Body focus conflicts with health flag '{flag}'",
                severity="warning",
                field="body_focus",
                current_value=u.body_focus,
            )

        if u.workout_types and discouraged_workout_types.intersection(set(u.workout_types)):
            _append_detail(
                warnings,
                code="health_workout_type_conflict",
                message=f"Workout type conflicts with health flag '{flag}'",
                severity="warning",
                field="workout_types",
                current_value=u.workout_types,
            )

        if u.intensity and u.intensity in discouraged_intensity:
            _append_detail(
                warnings,
                code="health_intensity_conflict",
                message=f"Intensity '{u.intensity}' may be unsuitable for health flag '{flag}'",
                severity="warning",
                field="intensity",
                current_value=u.intensity,
                suggested_value="low",
            )

    if "pregnancy" in set(u.health_flags) and u.user_sex == "male":
        _append_detail(
            warnings,
            code="pregnancy_male_conflict",
            message="pregnancy health flag conflicts with male user_sex",
            severity="warning",
            field="user_sex",
            current_value=u.user_sex,
        )


def _validate_location_equipment_consistency(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    if not u.location or not u.equipment:
        return
    rule = LOCATION_EQUIPMENT_COMPATIBILITY.get(u.location)
    if not rule:
        return
    discouraged = set(rule.get("discouraged_equipment", []))
    overlap = discouraged.intersection(set(u.equipment))
    if overlap:
        _append_detail(
            warnings,
            code="location_equipment_conflict",
            message=f"Equipment {sorted(overlap)} is unusual for location '{u.location}'",
            severity="warning",
            field="equipment",
            current_value=u.equipment,
        )


def _validate_schedule_preferences(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    if u.preferred_rest_days and u.preferred_workout_days:
        overlap = set(u.preferred_rest_days).intersection(set(u.preferred_workout_days))
        if overlap:
            _append_detail(
                errors,
                code="schedule_day_overlap",
                message=f"Same day cannot be both workout and rest preference: {sorted(overlap)}",
                field="preferred_rest_days",
                current_value=sorted(overlap),
            )

    if u.rest_days is not None and len(u.preferred_rest_days) > int(u.rest_days):
        _append_detail(
            warnings,
            code="too_many_preferred_rest_days",
            message="Number of preferred_rest_days exceeds rest_days",
            severity="warning",
            field="preferred_rest_days",
            current_value=u.preferred_rest_days,
            suggested_value=u.preferred_rest_days[: int(u.rest_days)],
        )

    if u.workouts_per_week is not None and len(u.preferred_workout_days) > int(u.workouts_per_week):
        _append_detail(
            warnings,
            code="too_many_preferred_workout_days",
            message="Number of preferred_workout_days exceeds workouts_per_week",
            severity="warning",
            field="preferred_workout_days",
            current_value=u.preferred_workout_days,
            suggested_value=u.preferred_workout_days[: int(u.workouts_per_week)],
        )


def _validate_session_time_against_workout_types(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    if u.time_per_session_minutes is None or not u.workout_types:
        return

    mins = int(u.time_per_session_minutes)
    for workout_type in u.workout_types:
        bounds = SESSION_TIME_HINTS.get(workout_type)
        if not bounds:
            continue
        lo, hi = bounds
        if mins < lo:
            _append_detail(
                warnings,
                code="session_time_short_for_type",
                message=f"Session time {mins} min is short for workout type '{workout_type}'",
                severity="warning",
                field="time_per_session_minutes",
                current_value=mins,
                suggested_value=lo,
            )
        elif mins > hi:
            _append_detail(
                warnings,
                code="session_time_long_for_type",
                message=f"Session time {mins} min is long for workout type '{workout_type}'",
                severity="warning",
                field="time_per_session_minutes",
                current_value=mins,
                suggested_value=hi,
            )


def _validate_notes_and_flags(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    note_text = _normalized_text(u.notes)
    if not note_text:
        return

    if _contains_pattern(note_text, STRICT_PHRASE_PATTERNS["exactly"]) and not u.rest_days_strict and u.rest_days is not None:
        _append_detail(
            warnings,
            code="notes_suggest_strict_rest",
            message="Notes contain strict wording but rest_days_strict is false",
            severity="warning",
            field="rest_days_strict",
            current_value=u.rest_days_strict,
            suggested_value=True,
        )

    if _contains_pattern(note_text, STRICT_PHRASE_PATTERNS["avoid_assumptions"]) and u.needs_clarification:
        _append_detail(
            warnings,
            code="strict_notes_with_clarification",
            message="Notes request strict interpretation but parser still marked needs_clarification",
            severity="warning",
            field="needs_clarification",
            current_value=u.needs_clarification,
        )


def _validate_internal_duplicates(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    fields = {
        "goals": u.inferred_goals,
        "equipment": u.equipment,
        "exclusions": u.exclusions,
        "body_focus": u.body_focus,
        "workout_types": u.workout_types,
        "health_flags": u.health_flags,
        "schedule_preferences": u.schedule_preferences,
        "preferred_rest_days": u.preferred_rest_days,
        "preferred_workout_days": u.preferred_workout_days,
    }
    for name, values in fields.items():
        if len(values) != len(set(values)):
            _append_detail(
                warnings,
                code="duplicate_values_detected",
                message=f"Duplicates found in {name}",
                severity="warning",
                field=name,
                current_value=values,
            )


def _validate_empty_signal_quality(u: PlanRequestUnderstanding, errors: list[ValidationDetail], warnings: list[ValidationDetail]) -> None:
    signal_count = 0
    if u.rest_days is not None:
        signal_count += 1
    if u.workouts_per_week is not None:
        signal_count += 1
    if u.time_per_session_minutes is not None:
        signal_count += 1
    if u.inferred_goals:
        signal_count += 1
    if u.workout_types:
        signal_count += 1
    if u.level:
        signal_count += 1
    if u.location:
        signal_count += 1

    if signal_count <= 1:
        _append_detail(
            warnings,
            code="low_information_request",
            message="Very few structured signals were extracted from the request",
            severity="warning",
        )


def _deduplicate_validation_details(items: list[ValidationDetail]) -> list[ValidationDetail]:
    seen: set[tuple[Any, ...]] = set()
    result: list[ValidationDetail] = []
    for item in items:
        key = (
            item.code,
            item.message,
            item.severity,
            item.field,
            str(item.current_value),
            str(item.suggested_value),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def normalize_understanding(understanding: PlanRequestUnderstanding) -> PlanRequestUnderstanding:
    return _normalize_understanding_values(understanding)


def build_validation_report(understanding: PlanRequestUnderstanding) -> ValidationReport:
    normalized = _normalize_understanding_values(understanding)
    errors: list[ValidationDetail] = []
    warnings: list[ValidationDetail] = []

    _validate_basic_ranges(normalized, errors, warnings)
    _validate_task_duration_consistency(normalized, errors, warnings)
    _validate_rest_frequency_consistency(normalized, errors, warnings)
    _validate_level_intensity_consistency(normalized, errors, warnings)
    _validate_goal_type_consistency(normalized, errors, warnings)
    _validate_health_constraints(normalized, errors, warnings)
    _validate_location_equipment_consistency(normalized, errors, warnings)
    _validate_schedule_preferences(normalized, errors, warnings)
    _validate_session_time_against_workout_types(normalized, errors, warnings)
    _validate_notes_and_flags(normalized, errors, warnings)
    _validate_internal_duplicates(normalized, errors, warnings)
    _validate_empty_signal_quality(normalized, errors, warnings)

    errors = _deduplicate_validation_details(errors)
    warnings = _deduplicate_validation_details(warnings)

    return ValidationReport(
        ok=not errors,
        normalized=normalized,
        errors=errors,
        warnings=warnings,
    )


def validate_plan_request(understanding: PlanRequestUnderstanding) -> None:
    report = build_validation_report(understanding)
    report.raise_for_errors()


def _coerce_day_name(index: int, item: dict[str, Any]) -> str:
    raw = _normalized_text(str((item or {}).get("day_name", "") or (item or {}).get("day", "") or ""))
    for canonical, aliases in WEEKDAY_ALIASES.items():
        candidates = [canonical] + aliases
        if raw in [_normalized_text(value) for value in candidates]:
            return canonical
    if raw:
        return raw
    weekday_order = [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ]
    return weekday_order[index % len(weekday_order)]


def _classify_distribution_item(item: dict[str, Any]) -> str:
    joined = " ".join(
        [
            str((item or {}).get("type", "")),
            str((item or {}).get("title", "")),
            str((item or {}).get("name", "")),
            str((item or {}).get("label", "")),
            str((item or {}).get("description", "")),
        ]
    )
    lower = _normalized_text(joined)

    for token in DISTRIBUTION_ACTIVITY_TOKENS["rest"]:
        if _normalized_text(token) in lower:
            return "rest"

    for token in DISTRIBUTION_ACTIVITY_TOKENS["recovery"]:
        if _normalized_text(token) in lower:
            return "recovery"

    for token in DISTRIBUTION_ACTIVITY_TOKENS["workout"]:
        if _normalized_text(token) in lower:
            return "workout"

    explicit_type = _normalized_text(str((item or {}).get("type", "")))
    if explicit_type in {"rest", "off", "rest_day", "day_off"}:
        return "rest"
    if explicit_type in {"recovery", "active_recovery", "mobility", "stretching"}:
        return "recovery"
    if explicit_type in {"workout", "training", "active", "cardio", "strength"}:
        return "workout"

    return "unknown"


def validate_plan_distribution_detailed(
    days: list[dict[str, Any]],
    *,
    total_days: int,
    rest_days: Optional[int],
    strict: bool,
    preferred_rest_days: Optional[list[str]] = None,
    preferred_workout_days: Optional[list[str]] = None,
) -> ValidationReport:
    base_understanding = PlanRequestUnderstanding(
        task_type="distribution_validation",
        total_days=int(total_days),
        rest_days=rest_days,
        rest_days_strict=bool(strict),
        inferred_goals=[],
        preferred_rest_days=list(preferred_rest_days or []),
        preferred_workout_days=list(preferred_workout_days or []),
    )
    report = build_validation_report(base_understanding)
    errors = list(report.errors)
    warnings = list(report.warnings)
    normalized = report.normalized

    if not isinstance(days, list):
        _append_detail(errors, code="distribution_not_list", message="days must be a list", field="days", current_value=type(days).__name__)
        return ValidationReport(ok=False, normalized=normalized, errors=errors, warnings=warnings)

    if len(days) != int(total_days):
        _append_detail(
            errors,
            code="distribution_length_mismatch",
            message="Number of day items must equal total_days",
            field="days",
            current_value=len(days),
            suggested_value=int(total_days),
        )

    seen_names: set[str] = set()
    rest_count = 0
    recovery_count = 0
    workout_count = 0

    actual_rest_names: list[str] = []
    actual_workout_names: list[str] = []

    for idx, item in enumerate(days):
        if not isinstance(item, dict):
            _append_detail(errors, code="distribution_item_not_object", message=f"days[{idx}] must be an object", field=f"days[{idx}]", current_value=type(item).__name__)
            continue

        day_name = _coerce_day_name(idx, item)
        if day_name in seen_names:
            _append_detail(
                warnings,
                code="duplicate_day_name",
                message=f"Day name '{day_name}' appears more than once",
                severity="warning",
                field=f"days[{idx}].day_name",
                current_value=day_name,
            )
        seen_names.add(day_name)

        kind = _classify_distribution_item(item)
        if kind == "rest":
            rest_count += 1
            actual_rest_names.append(day_name)
        elif kind == "recovery":
            recovery_count += 1
        elif kind == "workout":
            workout_count += 1
            actual_workout_names.append(day_name)
        else:
            _append_detail(
                warnings,
                code="unknown_day_kind",
                message=f"Could not classify day {idx + 1}",
                severity="warning",
                field=f"days[{idx}]",
            )

    if strict and rest_days is not None and rest_count != int(rest_days):
        _append_detail(
            errors,
            code="strict_rest_count_mismatch",
            message="Strict rest day count does not match expected rest_days",
            field="days",
            current_value=rest_count,
            suggested_value=int(rest_days),
        )

    if strict and rest_days is not None and len(days) == int(total_days):
        expected_workouts = int(total_days) - int(rest_days)
        if workout_count != expected_workouts:
            _append_detail(
                warnings,
                code="workout_count_mismatch",
                message="Workout day count does not match expected active days",
                severity="warning",
                field="days",
                current_value=workout_count,
                suggested_value=expected_workouts,
            )

    preferred_rest_days = _canonicalize_schedule_days(list(preferred_rest_days or []))
    preferred_workout_days = _canonicalize_schedule_days(list(preferred_workout_days or []))

    if preferred_rest_days:
        missing = [day for day in preferred_rest_days if day not in actual_rest_names]
        if missing:
            _append_detail(
                warnings,
                code="preferred_rest_days_not_respected",
                message=f"Preferred rest days not used: {missing}",
                severity="warning",
                field="preferred_rest_days",
                current_value=actual_rest_names,
            )

    if preferred_workout_days:
        missing = [day for day in preferred_workout_days if day not in actual_workout_names]
        if missing:
            _append_detail(
                warnings,
                code="preferred_workout_days_not_respected",
                message=f"Preferred workout days not used: {missing}",
                severity="warning",
                field="preferred_workout_days",
                current_value=actual_workout_names,
            )

    errors = _deduplicate_validation_details(errors)
    warnings = _deduplicate_validation_details(warnings)
    return ValidationReport(ok=not errors, normalized=normalized, errors=errors, warnings=warnings)


def validate_plan_distribution(
    days: list[dict[str, Any]],
    *,
    total_days: int,
    rest_days: Optional[int],
    strict: bool,
) -> bool:
    report = validate_plan_distribution_detailed(
        days,
        total_days=total_days,
        rest_days=rest_days,
        strict=strict,
    )
    return report.ok


def validation_report_to_dict(report: ValidationReport) -> dict[str, Any]:
    return {
        "ok": bool(report.ok),
        "normalized": understanding_to_dict(report.normalized),
        "errors": [
            {
                "code": item.code,
                "message": item.message,
                "severity": item.severity,
                "field": item.field,
                "current_value": item.current_value,
                "suggested_value": item.suggested_value,
            }
            for item in report.errors
        ],
        "warnings": [
            {
                "code": item.code,
                "message": item.message,
                "severity": item.severity,
                "field": item.field,
                "current_value": item.current_value,
                "suggested_value": item.suggested_value,
            }
            for item in report.warnings
        ],
    }


def validate_plan_request_to_dict(understanding: PlanRequestUnderstanding) -> dict[str, Any]:
    return validation_report_to_dict(build_validation_report(understanding))


def parse_and_validate_plan_request(prompt_text: str, meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    understanding = parse_plan_request(prompt_text, meta)
    report = build_validation_report(understanding)
    return validation_report_to_dict(report)
BODY_FOCUS_CATALOG: list[str] = [
    'abs_core',
    'chest',
    'back',
    'upper_back',
    'lower_back',
    'lats',
    'traps',
    'shoulders',
    'rear_delts',
    'front_delts',
    'side_delts',
    'biceps',
    'triceps',
    'forearms',
    'glutes',
    'quads',
    'hamstrings',
    'calves',
    'hips',
    'adductors',
    'abductors',
    'ankles',
    'knees',
    'wrists',
    'neck',
    'spine',
    'posture',
    'full_body',
    'push_chain',
    'pull_chain',
]


WORKOUT_TYPE_CATALOG: list[str] = [
    'walking',
    'running',
    'cycling',
    'swimming',
    'cardio',
    'strength',
    'full_body',
    'upper_body',
    'lower_body',
    'push_pull_legs',
    'hypertrophy',
    'powerlifting',
    'functional',
    'mobility',
    'stretching',
    'yoga',
    'pilates',
    'hiit',
    'plyometrics',
    'boxing',
    'combat',
    'dance',
    'rowing',
    'crossfit',
    'bodyweight',
    'rehab',
    'sports_specific',
    'core',
    'balance',
    'jumping',
    'sprinting',
    'circuits',
]


EQUIPMENT_CATALOG: list[str] = [
    'bodyweight',
    'dumbbells',
    'barbell',
    'bands',
    'kettlebell',
    'mat',
    'bench',
    'machine',
    'cable_machine',
    'smith_machine',
    'leg_press',
    'pullup_bar',
    'suspension_trainer',
    'medicine_ball',
    'jump_rope',
    'bike',
    'treadmill',
    'elliptical',
    'rowing_machine',
    'stairs',
    'foam_roller',
    'ankle_weights',
    'weighted_vest',
    'resistance_tube',
    'slam_ball',
    'wall_ball',
    'parallettes',
    'dip_station',
    'step_platform',
    'none',
]


HEALTH_FLAG_CATALOG: list[str] = [
    'back_pain',
    'knee_pain',
    'shoulder_pain',
    'neck_pain',
    'joint_pain',
    'obesity',
    'pregnancy',
    'postpartum',
    'hypertension',
    'heart_condition',
    'diabetes',
    'asthma',
    'low_fitness',
    'sedentary',
    'injury_history',
    'ankle_pain',
    'hip_pain',
    'wrist_pain',
    'elbow_pain',
    'fatigue',
    'poor_sleep',
    'stress',
    'anxiety',
]


GOAL_CATALOG: list[str] = [
    'lose_weight',
    'gain_muscle',
    'build_strength',
    'endurance',
    'mobility',
    'general_fitness',
    'tone_body',
    'athleticism',
    'posture',
    'rehab',
    'consistency',
    'energy',
    'stress_relief',
    'fat_loss',
    'muscle_definition',
]



def _warn_on_unknown_catalog_values(u: PlanRequestUnderstanding, warnings: list[ValidationDetail]) -> None:
    known_goal = set(GOAL_CATALOG) | set(GOAL_COMPATIBILITY_RULES.keys())
    known_workout = set(WORKOUT_TYPE_CATALOG) | set(WORKOUT_TYPE_IMPLICIT_LOAD.keys())
    known_equipment = set(EQUIPMENT_CATALOG)
    known_body_focus = set(BODY_FOCUS_CATALOG)
    known_health = set(HEALTH_FLAG_CATALOG) | set(HEALTH_CONTRA_RULES.keys())

    for goal in u.inferred_goals:
        if goal not in known_goal:
            _append_detail(
                warnings,
                code="unknown_goal_value",
                message=f"Unknown goal value '{goal}'",
                severity="warning",
                field="inferred_goals",
                current_value=goal,
            )

    for item in u.workout_types:
        if item not in known_workout:
            _append_detail(
                warnings,
                code="unknown_workout_type",
                message=f"Unknown workout type '{item}'",
                severity="warning",
                field="workout_types",
                current_value=item,
            )

    for item in u.equipment:
        if item not in known_equipment:
            _append_detail(
                warnings,
                code="unknown_equipment",
                message=f"Unknown equipment value '{item}'",
                severity="warning",
                field="equipment",
                current_value=item,
            )

    for item in u.body_focus:
        if item not in known_body_focus:
            _append_detail(
                warnings,
                code="unknown_body_focus",
                message=f"Unknown body_focus value '{item}'",
                severity="warning",
                field="body_focus",
                current_value=item,
            )

    for item in u.health_flags:
        if item not in known_health:
            _append_detail(
                warnings,
                code="unknown_health_flag",
                message=f"Unknown health flag '{item}'",
                severity="warning",
                field="health_flags",
                current_value=item,
            )


def build_validation_report(understanding: PlanRequestUnderstanding) -> ValidationReport:
    normalized = _normalize_understanding_values(understanding)
    errors: list[ValidationDetail] = []
    warnings: list[ValidationDetail] = []

    _validate_basic_ranges(normalized, errors, warnings)
    _validate_task_duration_consistency(normalized, errors, warnings)
    _validate_rest_frequency_consistency(normalized, errors, warnings)
    _validate_level_intensity_consistency(normalized, errors, warnings)
    _validate_goal_type_consistency(normalized, errors, warnings)
    _validate_health_constraints(normalized, errors, warnings)
    _validate_location_equipment_consistency(normalized, errors, warnings)
    _validate_schedule_preferences(normalized, errors, warnings)
    _validate_session_time_against_workout_types(normalized, errors, warnings)
    _validate_notes_and_flags(normalized, errors, warnings)
    _validate_internal_duplicates(normalized, errors, warnings)
    _validate_empty_signal_quality(normalized, errors, warnings)
    _warn_on_unknown_catalog_values(normalized, warnings)

    errors = _deduplicate_validation_details(errors)
    warnings = _deduplicate_validation_details(warnings)

    normalized.issues = [
        ParseIssue(code=item.code, message=item.message, severity=item.severity)
        for item in warnings
    ] + [
        ParseIssue(code=item.code, message=item.message, severity=item.severity)
        for item in errors
    ]

    normalized.needs_clarification = bool(normalized.needs_clarification or errors)

    return ValidationReport(
        ok=not errors,
        normalized=normalized,
        errors=errors,
        warnings=warnings,
    )

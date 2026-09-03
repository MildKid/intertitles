# Translating intertitles

Guidelines for volunteers. These go into the Crowdin project description too.

## What you are translating

Intertitles: the text cards that carry dialogue and narration in a silent film. Each
string in Crowdin is one card. The note above each string tells you the card type, who
is speaking, how many seconds the card is on screen, and any context from the editor.

## The rules

1. **Shorter than the English, or the same length.** The card is on screen for a fixed
   number of seconds and the audience has to read your text in that time. Spanish runs
   longer than English by default, so cut. Drop filler, choose the shorter synonym,
   prefer one clause where the English has two. The editor will send back translations
   that are too long for the card.
2. **Keep the line breaks.** A line break in the English is a line on the card. Match the
   number of lines when you can. Break at a natural pause.
3. **Match the register.** The films are American comedies from 1917 to 1924. The
   dialogue is plain, quick, and often a joke. Translate into everyday Mexican Spanish
   as spoken now, without slang that dates to a year, and without formal or literary
   Spanish. The Tramp and the projectionist are working people; the rich characters can
   sound a little stiff.
4. **Jokes must land, not translate.** A pun or wordplay in English needs a joke in
   Spanish at the same spot, even if the words are different. Use the context note; if
   it is unclear what the joke is, ask in the string's comments.
5. **Proper names stay.** Sherlock Jr., Harold, the Tramp. Place names too, unless the
   Spanish form is standard.
6. **Dialogue punctuation.** Keep the quotation style the card uses in English (the
   films use straight double quotes and dashes). Use ¿ and ¡ as normal Spanish does.
7. **Narrative cards** (scene-setting, "Meanwhile...") read like captions. Present tense,
   short.
8. **Insert cards** (letters, telegrams, signs, newspapers) are marked `insert`. Translate
   them normally; they may be subtitled rather than replaced.
9. **Do not translate credit cards** unless asked; leave the string empty.
10. **Ask.** Every string has a comment thread. A question there beats a guess.

## Reading speed, for reference

The editor's check flags any language over 17 characters per second on a card and
rejects over 25. A 3-second card can carry about 50 characters comfortably. Count
spaces.

## Style choices for this project

- `tú` between friends, lovers, and family; `usted` to strangers, employers, police,
  and from the working-class characters to the rich ones.
- Numbers as digits where the English uses digits.
- Currency stays dollars.

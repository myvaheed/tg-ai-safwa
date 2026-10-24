# The Safwa manifesto (draft)

**Safwa — Secure Agentic Framework for Wellbeing & Achievement.** A personal framework for
living by one's values together with an AI agent, in which the person owns everything that is
known about them. The bot in this repository is its reference implementation.

Status: a draft. The values and principles are taken from rules that already work in the bot;
their wording is still being worked on.

## Why

An AI assistant pulls a person's life into a chat. The chat is cleared, and with it go the
decisions made there: the person is left to remember them. The data about what they care for,
aim at, plan and lived through sits with a service, not with them. And most planning starts
from tasks, so the question of why a task is there is never asked.

Safwa starts from what matters to the person, lets the agent do the work, leaves every decision
and every record with the person, and keeps the picture of their life visible.

## Values

We value:

- **Values over tasks.** What matters to the person comes first; Goals and Actions follow from it.
- **Ownership over convenience.** A person's values, goals, plans and past belong to them.
- **Human decisions over agent autonomy.** The agent does the work; the person decides.
- **Screens over chat.** What can be seen stays; what was said in a chat is forgotten.
- **Rhythm and reflection over bursts of effort.** Short cycles, each ending in a look back.

While there is value in the items on the right, we value the items on the left more.

## Principles

### Values first

1. **Each level means one thing.** A Value is a direction the person lives by and is never done.
   A Goal is one concrete result with an observable Done. An Action fits in one day; what does
   not is a Goal. An ongoing practice is a repeating Action, or a Check that tracks it. A
   Check marks a fact yes or no: a state, a result or a habit done.
2. **An item serves the Value it makes more of, not the Value it needs.** Earning money can
   serve Freedom; needing perseverance to earn it does not make it serve Perseverance. What a
   result needs belongs under it, never above it.
3. **How vividly a Value is worded matters.** A short, vivid name and a few vivid sentences on
   why it matters and what the person does for it make clear what they want, and the agent
   helps word them from the person's own words.

### Owned and secured

4. **The person keeps their own data.** Their Values, Goals, plans and Diary are kept by them and
   encrypted with their own passphrase. The agent reads them to help and owns none of them.

### AI-first, the human decides

5. **The agent does the work.** It reads the whole picture, advises, checks and prepares every
   change, so the person spends attention on deciding rather than on filling forms.
6. **Nothing changes without the person's Save.** One Discard ends the whole chain of proposals,
   and the changes a request saved are written together or not at all.
7. **The agent asks rather than guesses.** It invents no goals, deadlines or reasons, and it does
   not argue with what the person named themselves.
8. **The agent checks quietly and speaks only when it matters.** After a Value, a Goal, an
   Action or a Check is created, the agent looks at it again and asks only when something is
   unclear.

### Visible

9. **Chat is the interface, not the memory.** A decision made in a chat lands in a structure the
   person can see on a screen: Home, the Dashboard, the Sprint, Today.
10. **The past is visible whole.** Each ended Sprint keeps its retro and draws its charts, Life in
   weeks shows the whole life week by week, and the Diary keeps each day in the person's own voice.

### Rhythm and reflection

11. **Work in Sprints of fixed length with Success criteria.** A running Sprint is not extended,
    paused or rewritten. It ends with a retro: Met or Not met, and one experiment for the next.
    Memory keeps the patterns the retros found, not grades.
12. **Plan strategically, work tactically.** Between Sprints the agent looks at the whole: every
    Value, how much open work serves it, and which Goals to take next. During a Sprint it looks
    at the Sprint alone: Today first, then the rest of it, and the Goals that work moves.
13. **Today is a small window.** An Action that stands in Today morning after morning is a signal
    to rethink it, not a failure. Rest is planned too: each Action names what it gives and what
    it costs.
14. **Observe state apart from work.** A Check is an observation, not a task, and no answer is not
    a Missed.

### Measure only what helps

15. **Effort Points and time are optional instruments.** The person switches them on when they
    help; without them the framework works on plain counts of Actions.

## The framework

- **Roles.** The Owner decides. The Advisor reads the workspace, advises and proposes. Hooks
  check quietly and raise what needs the Owner.
- **Events.** Planning, the Sprint, Today each morning, the Diary each day, the retro at the end of
  each Sprint.
- **Artifacts.** Values; the Goals, Subgoals and Actions, each under the Goals it serves; Checks; Memory; Life in weeks.

The rules behind each of these are in [DOMAIN.md](DOMAIN.md) and in the scenarios under `tests/brd/`.

## Open questions

- The final wording of the values and the principles.
- How the ownership principle treats a model run by a remote provider, which reads what it is sent.
- What counts as evidence that the framework works: the author's own Sprints first, then others'.
- Where the method ends and the bot begins: which principles any tool can carry, and which need
  the agent.

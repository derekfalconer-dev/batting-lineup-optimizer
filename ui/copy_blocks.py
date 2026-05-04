# ui/copy_blocks.py
import streamlit as st

def render_how_to_use_panel() -> None:
    with st.container(border=True):
        st.markdown("### How coaches are using this")
        st.caption("The most common decisions this tool helps with right now.")

        col1, col2, col3, col4, col5 = st.columns(5)

        with col1:
            st.markdown("**1. Adjust for a hot or cold bat**")
            st.caption(
                "Use the nudge sliders to reflect a player who’s hot or in a slump, then re-run the optimizer to see if it actually changes where player should hit."
            )

        with col2:
            st.markdown("**2. Matchup against today’s pitcher**")
            st.caption(
                "Use nudges when you know a hitter matches up especially well or poorly against the opposing pitcher, "
                "then re-optimize to see if the lineup should change."
            )

        with col3:
            st.markdown("**3. Player absent tonight**")
            st.caption(
                "Bench the absent player, then optimize or simulate again to see how the order should shift."
            )

        with col4:
            st.markdown("**4. Try a new player**")
            st.caption(
                "Add a player from an archetype, place the player in the order, and simulate how the player changes the lineup."
            )

        with col5:
            st.markdown("**5. Compare your intuition**")
            st.caption(
                "Set up the order you like, simulate it, then compare it against the optimized order."
            )


def render_model_limitations_panel() -> None:
    with st.expander("Model & Limitations", expanded=False):
        st.markdown(
            """
**What this tool is doing**
- It uses Monte Carlo simulation to play out many versions of the game and estimate run scoring outcomes.
- It focuses on lineup-level outputs like average runs, median runs, and the chance of scoring at least a target number of runs.
- It adjusts the environment based on your game settings such as inning length, run cap, diamond size, leadoffs, strategy, coaching style, and opponent strength.

**What the player data means**
- GameChanger imports are treated as directional input, not perfect truth.
- The app converts GameChanger batting stats into internal 0–100 player traits, then builds simulator probabilities from those traits.
- Coach edits and archetype players are meant to help when GameChanger data is sparse, noisy, or missing.
- Each player may show plate appearances, number of source files, and a confidence label.
- Low confidence does not mean “do not use.” It means the imported data is a weaker baseline and Coach Lab review is recommended.
- When confidence is low, the best workflow is to inspect that player, make a small trait adjustment or choose a better-fit archetype, then re-run the simulation.

**Important limitations**
- Bad scorekeeping will still affect the imported baseline.
- Small sample sizes can make the model noisy for individual players.
- This is better for comparing lineup ideas than for pretending to predict exact game outcomes.
- The optimized lineup is the best lineup found by the current fast search settings, not a mathematical proof that no better lineup exists.

**Best use cases**
- Rebuilding the order when a player is absent
- Stress-testing your intuition lineup vs an optimized lineup
- Seeing whether one weak bat or one added bat materially changes the offense
- Getting directional guidance before making a final coaching call
- Using imported stats as a baseline, then tightening up low-confidence players with coach knowledge
            """
        )

def render_model_limitations() -> None:
    st.markdown("### How this tool works")
    st.write(
        "This tool builds a player profile for each hitter, then plays out many simulated games "
        "to compare batting orders under the same rules and game conditions."
    )

    st.markdown("### What it is best used for")
    st.write(
        "It is best used to compare lineup ideas and see which batting orders tend to score more over time."
    )
    st.write(
        "It is a coaching decision aid, not a promise of the exact score in your next game."
    )

    st.markdown("### How player edits affect the simulation")
    rows = [
        {
            "Slider / Trait": "Contact",
            "What it does": "Helps a hitter put the ball in play more often, get more singles, and strike out less.",
        },
        {
            "Slider / Trait": "Power",
            "What it does": "Raises extra-base hit and home run upside.",
        },
        {
            "Slider / Trait": "Speed",
            "What it does": "Helps with steals, pressure on the defense, and taking extra bases.",
        },
        {
            "Slider / Trait": "Baserunning",
            "What it does": "Helps runners make better decisions and take extra bases more often.",
        },
        {
            "Slider / Trait": "Plate Discipline",
            "What it does": "Helps a hitter work better at-bats and draw more walks.",
        },
        {
            "Slider / Trait": "Strikeout Tendency",
            "What it does": "Higher values mean the hitter strikes out more often.",
        },
        {
            "Slider / Trait": "Walk Skill",
            "What it does": "Raises walk rate directly.",
        },
        {
            "Slider / Trait": "Aggression",
            "What it does": "Makes runners more willing to push the action on the bases.",
        },
        {
            "Slider / Trait": "Sacrifice Ability",
            "What it does": "Supports small-ball and move-the-runner style play.",
        },
        {
            "Slider / Trait": "Chase Tendency",
            "What it does": "Tracked in the player profile, but not yet a major direct driver in the simulation.",
        },
        {
            "Slider / Trait": "Clutch",
            "What it does": "Stored in the player profile, but not yet a major direct driver in the simulation.",
        },
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)

    st.markdown("### How game settings affect the simulation")
    game_rows = [
        {
            "Setting": "Game Strategy",
            "What it does": "Small Ball leans more toward pressure and runner movement. Power leans more toward damage. Balanced stays in the middle.",
        },
        {
            "Setting": "Coaching Style",
            "What it does": "Changes how aggressive the team is on the bases.",
        },
        {
            "Setting": "Opposing Pitching Strength",
            "What it does": "Makes it easier or harder to make contact, draw walks, and do damage.",
        },
        {
            "Setting": "Opponent Level",
            "What it does": "Changes how easy it is to take extra bases and move runners.",
        },
        {
            "Setting": "Continuous Batting",
            "What it does": "If turned on, the full active roster bats. If turned off, only the top part of the lineup bats.",
        },
        {
            "Setting": "Inning Run Limit",
            "What it does": "Caps how many runs can score in one inning, which matters a lot in youth baseball.",
        },
        {
            "Setting": "Diamond Size",
            "What it does": "Smaller diamonds usually increase steals, speed value, and overall pressure on the defense.",
        },
        {
            "Setting": "Leadoffs Allowed",
            "What it does": "Greatly increases running pressure and usually reduces double-play chances.",
        },
    ]
    st.dataframe(game_rows, use_container_width=True, hide_index=True)

    st.markdown("### Important limits")
    limits_rows = [
        {
            "Area": "Exact score prediction",
            "What to know": "This tool compares lineups. It does not predict the exact score of a real game.",
        },
        {
            "Area": "GameChanger data quality",
            "What to know": "If the scorebook is wrong, the player profile can be off until the coach adjusts it.",
        },
        {
            "Area": "Batted-ball detail",
            "What to know": "It does not directly know each hitter's true ground-ball or fly-ball tendency from GameChanger.",
        },
        {
            "Area": "Youth baseball chaos",
            "What to know": "It does not separately model every overthrow, missed tag, or weird youth-baseball play.",
        },
        {
            "Area": "Defense and pitching",
            "What to know": "It does not fully model defensive positioning, pitcher fatigue, or detailed matchup effects.",
        },
        {
            "Area": "Double plays and fielder's choice",
            "What to know": "These are included only in a simplified way.",
        },
    ]
    st.dataframe(limits_rows, use_container_width=True, hide_index=True)

    st.markdown("### Best way to use it")
    st.write(
        "The best question to ask is: 'Does this lineup usually look stronger than my other options?'"
    )

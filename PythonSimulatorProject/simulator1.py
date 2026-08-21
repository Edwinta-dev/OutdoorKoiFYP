import os
import customtkinter as ctk
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk

# Configure CustomTkinter Theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class PondSimulatorUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Phase 1 - No Monitoring vs Reactive vs Active Monitoring Pond Simulator")
        self.geometry("1400x900")

        # Internal State
        self.df_data = None
        self.sliders = {}
        self.date_column_name = None
        self.data_quality_note = ""

        # Configure Grid Layout (Sidebar left, Main Content right)
        self.grid_columnconfigure(0, weight=0)
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_main_screen()

    # ==========================================
    # 1. SIDEBAR CONFIGURATION & STATS
    # ==========================================
    def _build_sidebar(self):
        self.sidebar = ctk.CTkScrollableFrame(self, width=340, label_text="Configuration Parameters")
        self.sidebar.grid(row=0, column=0, padx=10, pady=10, sticky="nsew")

        # --- CONFIG SLIDERS ---
        self._add_slider("adverse_rain_mm", "1. Adverse Rain Threshold (mm)", 10, 100, 30, is_int=True)
        self._add_slider("rain_runoff_mm", "2. Natural Rain Runoff / Drainage (mm/day)", 0.0, 10.0, 2.0, is_int=False)
        self._add_slider("reactive_cycle_days", "3. Reactive/Control Monthly Cycle (Days)", 7, 60, 30, is_int=True)
        self._add_slider("active_cycle_days", "4. Active Monitoring Monthly Cycle (Days)", 7, 60, 30, is_int=True)
        self._add_slider("active_trigger_pct", "5. Active Monitoring Trigger (% of Adverse)", 10, 90, 50, is_int=True)
        self._add_slider("active_change_pct", "6. Active Monitoring Water Change (%)", 5, 50, 20, is_int=True)
        self._add_slider("damage_scalar", "7. Adverse Damage Scalar (weak-pond vulnerability)", 0.1, 5.0, 1.0, is_int=False)
        self._add_slider("monthly_change_pct", "8. Monthly Water Change (%)", 10, 100, 25, is_int=True)

        # 9. Type of Decay Dropdown
        lbl_decay = ctk.CTkLabel(self.sidebar, text="9. Adverse Zone Decay Type:", font=("Inter", 12, "bold"))
        lbl_decay.pack(anchor="w", padx=10, pady=(10, 0))
        self.combo_decay = ctk.CTkComboBox(
            self.sidebar,
            values=["linear", "quadratic", "exponential", "cubic"],
            command=lambda choice: self._on_parameter_change()
        )
        self.combo_decay.set("linear")
        self.combo_decay.pack(fill="x", padx=10, pady=(0, 10))

        # 10. Default Pond Health Decay
        self._add_slider("default_decay", "10. Default Organic Decay (%/day)", 0.1, 3.0, 0.5, is_int=False)

        ctk.CTkFrame(self.sidebar, height=2, fg_color="gray30").pack(fill="x", padx=10, pady=15)

        # --- SUMMARY STATISTICS PANEL ---
        lbl_stats_header = ctk.CTkLabel(self.sidebar, text="\U0001F4CA Summary Statistics (Full Dataset)", font=("Inter", 13, "bold"), text_color="#3a86ff")
        lbl_stats_header.pack(anchor="w", padx=10, pady=(5, 5))

        self.stats_frame = ctk.CTkFrame(self.sidebar, corner_radius=8, fg_color="#1e1e1e")
        self.stats_frame.pack(fill="x", padx=5, pady=5)

        self.lbl_stats_content = ctk.CTkLabel(
            self.stats_frame,
            text="Upload dataset to view statistics.",
            font=("Courier", 11),
            justify="left",
            anchor="w"
        )
        self.lbl_stats_content.pack(padx=10, pady=10, fill="x")

    def _add_slider(self, key, label_text, from_val, to_val, default_val, is_int=True):
        lbl = ctk.CTkLabel(self.sidebar, text=f"{label_text}: {default_val}", font=("Inter", 12, "bold"))
        lbl.pack(anchor="w", padx=10, pady=(10, 0))

        steps = int(to_val - from_val) if is_int else int((to_val - from_val) * 10)
        slider = ctk.CTkSlider(
            self.sidebar, from_=from_val, to=to_val, number_of_steps=steps,
            command=lambda val: self._update_slider_label(key, label_text, val, is_int)
        )
        slider.set(default_val)
        slider.pack(fill="x", padx=10, pady=(0, 5))
        self.sliders[key] = {"widget": slider, "label": lbl, "is_int": is_int}

    def _update_slider_label(self, key, label_text, val, is_int):
        formatted_val = int(val) if is_int else round(val, 2)
        self.sliders[key]["label"].configure(text=f"{label_text}: {formatted_val}")
        self._on_parameter_change()

    # ==========================================
    # 2. MAIN SCREEN & GRAPH CHECKBOXES
    # ==========================================
    def _build_main_screen(self):
        self.main_container = ctk.CTkFrame(self, fg_color="transparent")
        self.main_container.grid(row=0, column=1, padx=10, pady=10, sticky="nsew")

        self.main_container.grid_columnconfigure(0, weight=1)
        self.main_container.grid_rowconfigure(0, weight=3)  # Top 30% Rainfall
        self.main_container.grid_rowconfigure(1, weight=7)  # Bottom 70% Output

        # --- TOP ZONE (30%): Rainfall Bar Chart Frame ---
        self.top_zone = ctk.CTkFrame(self.main_container, corner_radius=12)
        self.top_zone.grid(row=0, column=0, padx=5, pady=5, sticky="nsew")
        self.top_zone.grid_propagate(False)

        self.drop_label = ctk.CTkLabel(
            self.top_zone,
            text="\U0001F4C1 Click Here to Load Historical Rainfall CSV File",
            font=("Inter", 14, "bold"),
            text_color="gray60",
            cursor="hand2"
        )
        self.drop_label.pack(expand=True, fill="both", padx=20, pady=20)
        self.drop_label.bind("<Button-1>", lambda e: self._browse_file())

        # PRE-CREATE TOP RAINFALL FIGURE ONCE TO PREVENT MEMORY LEAKS
        plt.style.use("dark_background")
        self.fig_rain, self.ax_rain = plt.subplots(figsize=(8, 2), dpi=100)
        self.fig_rain.patch.set_facecolor('#2b2b2b')
        self.ax_rain.set_facecolor('#1e1e1e')
        self.canvas_rain = None

        # --- BOTTOM ZONE (70%): Output Graph & Checkboxes ---
        self.bottom_zone = ctk.CTkFrame(self.main_container, corner_radius=12)
        self.bottom_zone.grid(row=1, column=0, padx=5, pady=5, sticky="nsew")

        # Graph View Controls
        ctrl_bar = ctk.CTkFrame(self.bottom_zone, fg_color="transparent")
        ctrl_bar.pack(fill="x", padx=10, pady=(10, 0))

        lbl_toggle = ctk.CTkLabel(ctrl_bar, text="Plot Visibility:", font=("Inter", 12, "bold"))
        lbl_toggle.pack(side="left", padx=(5, 15))

        self.chk_show_control = ctk.CTkCheckBox(
            ctrl_bar, text="No Monitoring (Control)", command=self._on_parameter_change,
            fg_color="#8d99ae", hover_color="#6b7686"
        )
        self.chk_show_control.select()
        self.chk_show_control.pack(side="left", padx=10)

        self.chk_show_reactive = ctk.CTkCheckBox(
            ctrl_bar, text="Reactive (Crash-Triggered)", command=self._on_parameter_change,
            fg_color="#e63946", hover_color="#b82a36"
        )
        self.chk_show_reactive.select()
        self.chk_show_reactive.pack(side="left", padx=10)

        self.chk_show_active = ctk.CTkCheckBox(
            ctrl_bar, text="Active Monitoring (Threshold)", command=self._on_parameter_change,
            fg_color="#2a9d8f", hover_color="#1d6f65"
        )
        self.chk_show_active.select()
        self.chk_show_active.pack(side="left", padx=10)

        # PRE-CREATE OUTPUT FIGURE ONCE
        self.fig_out, self.ax_out = plt.subplots(figsize=(8, 5), dpi=100)
        self.fig_out.patch.set_facecolor('#2b2b2b')
        self.ax_out.set_facecolor('#1e1e1e')
        self.ax_out.text(0.5, 0.5, "Awaiting Data Upload & Configuration...",
                          ha='center', va='center', color='gray')
        self.ax_out.set_xticks([])
        self.ax_out.set_yticks([])

        self.canvas_out = FigureCanvasTkAgg(self.fig_out, master=self.bottom_zone)
        self.canvas_out.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=(10, 0))

        # Standard matplotlib zoom/pan toolbar -- this replaces the old custom
        # date-range slider/entry filter. It only changes the VIEW (no
        # recompute), so it can't trigger the crash-on-drag behaviour the old
        # date gating had, and it works on the full simulated series.
        toolbar_frame = ctk.CTkFrame(self.bottom_zone, fg_color="transparent", height=32)
        toolbar_frame.pack(fill="x", padx=10, pady=(0, 8))
        self.nav_toolbar = NavigationToolbar2Tk(self.canvas_out, toolbar_frame)
        self.nav_toolbar.update()

    # ==========================================
    # 3. FILE LOADING
    # ==========================================
    def _browse_file(self):
        file_path = ctk.filedialog.askopenfilename(filetypes=[("CSV Files", "*.csv")])
        if file_path:
            self._load_dataset(file_path)

    def _load_dataset(self, file_path):
        try:
            df = pd.read_csv(file_path)

            # Identify rainfall column
            rain_col = [c for c in df.columns if 'rain' in c.lower() or 'total' in c.lower()]
            rain_numeric = pd.to_numeric(df[rain_col[0]], errors='coerce') if rain_col else pd.to_numeric(df.iloc[:, 0], errors='coerce')
            n_missing_rain = int(rain_numeric.isna().sum())
            rain_series = rain_numeric.fillna(0.0)

            # Identify date column if present
            date_col = [c for c in df.columns if 'date' in c.lower() or 'day' in c.lower() or 'time' in c.lower()]
            if date_col:
                self.date_column_name = date_col[0]
                dt_parsed = pd.to_datetime(df[date_col[0]], dayfirst=True, errors='coerce')
            else:
                self.date_column_name = None
                dt_parsed = pd.Series([pd.NaT] * len(df))

            working = pd.DataFrame({'_dt': dt_parsed, 'rainfall_mm': rain_series})

            # Guard against silently mixing multiple weather stations together.
            # If more than one station is present, keep only the one with the
            # most records rather than interleaving rows from different sites.
            station_note = ""
            if 'station' in df.columns:
                counts = df['station'].value_counts()
                if len(counts) > 1:
                    dominant = counts.idxmax()
                    working = working[(df['station'] == dominant).values]
                    station_note = f" | {len(counts)} stations in file, using '{dominant}' ({counts.max()} rows)"
                elif len(counts) == 1:
                    station_note = f" | Station: {counts.index[0]}"

            # Sort chronologically and measure calendar gaps so the day-index
            # simulation loop's "consecutive day" assumption is disclosed
            # rather than silently wrong.
            if self.date_column_name:
                working = working.dropna(subset=['_dt']).sort_values('_dt').reset_index(drop=True)
                gap_days = 0
                if len(working) > 1:
                    full_range = pd.date_range(working['_dt'].min(), working['_dt'].max())
                    gap_days = len(full_range) - len(working)
                date_range_note = f"{working['_dt'].min().date()} to {working['_dt'].max().date()}"
                dates_display = working['_dt'].dt.strftime('%Y-%m-%d')
            else:
                working = working.reset_index(drop=True)
                gap_days = 0
                date_range_note = "no date column found (using row order)"
                dates_display = pd.Series([f"Day {i + 1}" for i in range(len(working))])

            self.df_data = pd.DataFrame({'date': dates_display.values, 'rainfall_mm': working['rainfall_mm'].values})

            self.data_quality_note = (
                f"Loaded {len(self.df_data)} days ({date_range_note}){station_note}\n"
                f"Missing rainfall readings treated as 0mm: {n_missing_rain} | "
                f"Calendar gap-days not in data: {gap_days}"
            )

            # Mount canvas for top plot once if not already mounted
            if self.canvas_rain is None:
                self.drop_label.destroy()
                self.canvas_rain = FigureCanvasTkAgg(self.fig_rain, master=self.top_zone)
                self.canvas_rain.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

            self._display_top_rainfall_graph()
            self._on_parameter_change()

        except Exception as e:
            self.drop_label.configure(text=f"❌ Error loading file: {str(e)}", text_color="red")

    def _display_top_rainfall_graph(self):
        self.ax_rain.clear()

        days = range(1, len(self.df_data) + 1)
        self.ax_rain.bar(days, self.df_data['rainfall_mm'], color='#3a86ff', alpha=0.85)
        self.ax_rain.set_title(f"Rainfall Profile (Full Dataset, {len(self.df_data)} Days)", fontsize=10, color="white")
        self.ax_rain.set_ylabel("Rain (mm)", fontsize=8)
        self.ax_rain.grid(True, linestyle=":", alpha=0.3)
        self.fig_rain.tight_layout()

        if self.canvas_rain:
            self.canvas_rain.draw()

    # ==========================================
    # 4. COMPUTE & REDRAW ENGINE
    # ==========================================
    def _on_parameter_change(self):
        if self.df_data is None:
            return

        config = {key: meta["widget"].get() for key, meta in self.sliders.items()}
        config["decay_type"] = self.combo_decay.get()

        res_control, res_reactive, res_active = compute_simulation(self.df_data, config)

        self._update_statistics_display(config, res_control, res_reactive, res_active)
        self._render_output_graph(res_control, res_reactive, res_active)

    def _update_statistics_display(self, config, res_c, res_r, res_a):
        # Flag configurations where a pond would spiral down from organic
        # decay ALONE (zero rain, ever) -- this used to be silently true
        # under the old defaults and confounded "rain damage" with "the
        # maintenance schedule can't keep up regardless of rain".
        warnings = []
        if config['default_decay'] * config['reactive_cycle_days'] > config['monthly_change_pct']:
            warnings.append("Reactive/Control cycle can't hold pond health even with ZERO rain (decay > monthly restore)")
        if config['default_decay'] * config['active_cycle_days'] > config['monthly_change_pct']:
            warnings.append("Active Monitoring cycle can't hold pond health even with ZERO rain (decay > monthly restore)")
        warning_block = ("\n".join(f"⚠ {w}" for w in warnings) + "\n\n") if warnings else ""

        quality_block = (self.data_quality_note + "\n\n") if self.data_quality_note else ""

        stats_text = (
            quality_block +
            warning_block +
            f"=== NO MONITORING (CONTROL) ===\n"
            f"• Days Health < 0%  : {res_c['adverse_days']} days\n"
            f"• Worst Health Reached: {res_c['min_health']:.1f}%\n"
            f"• Avg Pond Health   : {res_c['avg_health']:.1f}%\n"
            f"• Total Water Used  : {res_c['water_used']:.0f}%\n\n"
            f"=== REACTIVE (CRASH-TRIGGERED) ===\n"
            f"• Days Health < 0%  : {res_r['adverse_days']} days\n"
            f"• Worst Health Reached: {res_r['min_health']:.1f}%\n"
            f"• Avg Pond Health   : {res_r['avg_health']:.1f}%\n"
            f"• Total Water Used  : {res_r['water_used']:.0f}%\n"
            f"• Emergency Resets  : {res_r['adverse_breaches']}\n\n"
            f"=== ACTIVE MONITORING (THRESHOLD) ===\n"
            f"• Days Health < 0%  : {res_a['adverse_days']} days\n"
            f"• Worst Health Reached: {res_a['min_health']:.1f}%\n"
            f"• Avg Pond Health   : {res_a['avg_health']:.1f}%\n"
            f"• Total Water Used  : {res_a['water_used']:.0f}%\n"
            f"• Emergency Resets  : {res_a['adverse_breaches']}"
        )
        self.lbl_stats_content.configure(text=stats_text)

    def _render_output_graph(self, res_control, res_reactive, res_active):
        self.ax_out.clear()

        days = [i + 1 for i in range(len(res_control["health"]))]

        show_c = self.chk_show_control.get()
        show_r = self.chk_show_reactive.get()
        show_a = self.chk_show_active.get()

        if show_c:
            self.ax_out.plot(days, res_control["health"], color="#8d99ae", linewidth=1.5, label="No Monitoring (Control)")
        if show_r:
            self.ax_out.plot(days, res_reactive["health"], color="#e63946", linewidth=1.5, label="Reactive (Crash-Triggered)")
        if show_a:
            self.ax_out.plot(days, res_active["health"], color="#2a9d8f", linewidth=1.5, label="Active Monitoring (Threshold)")

        self.ax_out.axhline(0, color="orange", linestyle="--", alpha=0.7, label="Adverse Boundary (0%)")
        self.ax_out.set_title("Pond Health Trajectory Comparison", fontsize=12, color="white")
        self.ax_out.set_xlabel("Timeline (Days)", fontsize=9)
        self.ax_out.set_ylabel("Pond Health Metric (%)", fontsize=9)
        self.ax_out.grid(True, linestyle=":", alpha=0.4)
        if show_c or show_r or show_a:
            self.ax_out.legend(loc="upper right")

        self.fig_out.tight_layout()
        self.canvas_out.draw()


# ==========================================
# 5. COMPUTE ENGINE (Phase 1: No Monitoring / Reactive / Active Monitoring)
# ==========================================
def run_pond_step(current_health, daily_rain_mm, config, model_type, state):
    """
    Evaluates 1 day tick for a single simulated pond.

    model_type: 'CONTROL' | 'REACTIVE' | 'ACTIVE'
      - CONTROL  : Never looks at rainfall at all -- health only ever moves
                   from organic decay and the fixed monthly water-change
                   cycle. This is the "a user completely ignores rain"
                   baseline, used specifically to isolate how much of the
                   health loss elsewhere is actually attributable to rain
                   (as opposed to routine upkeep alone).
      - REACTIVE : Same underlying maintenance as CONTROL, but ALSO forces a
                   full 100% emergency water change the day AFTER accumulated
                   rain crosses the adverse threshold -- i.e. the pond has
                   visibly crashed and the owner responds to the symptom.
                   This owner is not reading a rain gauge; they're reacting
                   to a dead-looking pond.
      - ACTIVE   : REACTIVE, plus a smaller pre-emptive top-up once
                   accumulated rain crosses a configurable fraction of the
                   adverse threshold -- i.e. the owner has a live rain/EC/pH
                   reading and acts before the crash. This is still same-day
                   sensing, not a forecast -- forecast-based lead time is a
                   separate, later phase.
    """
    # 1. Base organic daily decay (applies identically to every model)
    current_health -= config['default_decay']

    # 2. Track accumulated rainfall in the pond, net of a configurable daily
    #    natural runoff/overflow/evaporation allowance.
    state['accumulated_rain_mm'] = max(0.0, state['accumulated_rain_mm'] - config['rain_runoff_mm']) + daily_rain_mm

    # 3. ADVERSE ZONE DECAY -- deliberately skipped for CONTROL.
    if model_type != 'CONTROL' and state['accumulated_rain_mm'] > config['adverse_rain_threshold_mm']:
        excess_rain = state['accumulated_rain_mm'] - config['adverse_rain_threshold_mm']

        if config['decay_type'] == 'linear':
            base_damage = excess_rain * 3.0
        elif config['decay_type'] == 'quadratic':
            base_damage = (excess_rain ** 2) * 0.5
        elif config['decay_type'] == 'exponential':
            base_damage = (1.5 ** excess_rain)
        elif config['decay_type'] == 'cubic':
            base_damage = (excess_rain ** 3) * 0.05
        else:
            base_damage = excess_rain * 3.0

        # Damage Scalar: a pond that is ALREADY marginal takes proportionally
        # more damage from the same rain event than a pond that was mostly
        # healthy going in (a healthy pond -> marginal effect, a barely
        # healthy pond -> extremely toxic effect, scaled by this slider).
        health_frac = max(0.0, min(100.0, current_health)) / 100.0
        vulnerability = 1.0 + config['damage_scalar'] * (1.0 - health_frac)
        rain_damage = base_damage * vulnerability

        current_health -= rain_damage
        state['is_adverse'] = True

    # 4. DECISION ENGINE
    water_changed_pct = 0.0

    # A. Emergency Reset (day after entering the adverse zone) -- REACTIVE & ACTIVE only
    if model_type != 'CONTROL' and state['was_adverse_yesterday']:
        water_changed_pct = 100.0
        current_health = 100.0  # Full reset
        state['accumulated_rain_mm'] = 0.0
        state['is_adverse'] = False
        state['days_since_last_change'] = 0
        state['adverse_breaches'] += 1

    # B. Pre-emptive partial intervention -- ACTIVE MONITORING only
    elif model_type == 'ACTIVE' and (state['accumulated_rain_mm'] >= config['active_rain_trigger_mm']):
        water_changed_pct = config['active_change_pct']
        current_health = min(100.0, current_health + water_changed_pct)
        state['accumulated_rain_mm'] *= (1.0 - (water_changed_pct / 100.0))
        state['days_since_last_change'] = 0

    # C. Default maintenance cycle -- applies to all three models
    elif state['days_since_last_change'] >= config[f'{model_type.lower()}_monthly_days']:
        water_changed_pct = config['monthly_change_pct']
        current_health = min(100.0, current_health + water_changed_pct)
        state['accumulated_rain_mm'] *= (1.0 - (water_changed_pct / 100.0))
        state['days_since_last_change'] = 0

    # Track days spent below 0% health
    if current_health < 0.0:
        state['adverse_days_count'] += 1

    # Update state trackers
    state['was_adverse_yesterday'] = state['is_adverse']
    state['days_since_last_change'] += 1

    return max(-100.0, current_health), water_changed_pct, state


def compute_simulation(df, config):
    days = len(df)
    rain_data = df['rainfall_mm'].values

    model_config = {
        'adverse_rain_threshold_mm': config['adverse_rain_mm'],
        'rain_runoff_mm': config['rain_runoff_mm'],
        'reactive_monthly_days': config['reactive_cycle_days'],
        'control_monthly_days': config['reactive_cycle_days'],
        'active_monthly_days': config['active_cycle_days'],
        'active_rain_trigger_mm': config['adverse_rain_mm'] * (config['active_trigger_pct'] / 100.0),
        'active_change_pct': config['active_change_pct'],
        'damage_scalar': config['damage_scalar'],
        'monthly_change_pct': config['monthly_change_pct'],
        'decay_type': config['decay_type'],
        'default_decay': config['default_decay'],
    }

    model_types = ('CONTROL', 'REACTIVE', 'ACTIVE')
    states = {
        m: {
            'accumulated_rain_mm': 0.0, 'is_adverse': False, 'was_adverse_yesterday': False,
            'days_since_last_change': 0, 'adverse_days_count': 0, 'adverse_breaches': 0
        } for m in model_types
    }
    health = {m: 100.0 for m in model_types}
    hist = {m: [] for m in model_types}
    water = {m: 0.0 for m in model_types}

    for day_idx in range(days):
        daily_rain = rain_data[day_idx]
        for m in model_types:
            health[m], w, states[m] = run_pond_step(health[m], daily_rain, model_config, m, states[m])
            hist[m].append(health[m])
            water[m] += w

    def package(m):
        return {
            "health": hist[m],
            "water_used": water[m],
            "adverse_days": states[m]['adverse_days_count'],
            "avg_health": float(np.mean(hist[m])),
            "min_health": float(np.min(hist[m])),
            "adverse_breaches": states[m]['adverse_breaches'],
        }

    return package('CONTROL'), package('REACTIVE'), package('ACTIVE')


if __name__ == "__main__":
    app = PondSimulatorUI()
    app.mainloop()
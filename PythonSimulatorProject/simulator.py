import os
import customtkinter as ctk
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# Configure CustomTkinter Theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


class PondSimulatorUI(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("Predictive vs Reactive Pond Simulator Engine")
        self.geometry("1350x880")

        # Internal State
        self.df_data = None
        self.sliders = {}
        self.date_column_name = None

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
        self._add_slider("reactive_cycle_days", "2. Reactive Monthly Cycle (Days)", 7, 60, 30, is_int=True)
        self._add_slider("predictive_cycle_days", "3. Predictive Monthly Cycle (Days)", 7, 60, 30, is_int=True)
        self._add_slider("predictive_trigger_pct", "4. Predictive Trigger (% of Adverse)", 10, 90, 50, is_int=True)
        self._add_slider("predictive_change_pct", "5. Predictive Water Change (%)", 5, 50, 20, is_int=True)
        self._add_slider("recovery_scalar", "6. Adverse Damage Scalar", 0.1, 5.0, 1.0, is_int=False)
        self._add_slider("monthly_change_pct", "7. Monthly Water Change (%)", 10, 100, 25, is_int=True)

        # 8. Type of Decay Dropdown
        lbl_decay = ctk.CTkLabel(self.sidebar, text="8. Adverse Zone Decay Type:", font=("Inter", 12, "bold"))
        lbl_decay.pack(anchor="w", padx=10, pady=(10, 0))
        self.combo_decay = ctk.CTkComboBox(
            self.sidebar, 
            values=["linear", "quadratic", "exponential", "cubic"],
            command=lambda choice: self._on_parameter_change()
        )
        self.combo_decay.set("linear")
        self.combo_decay.pack(fill="x", padx=10, pady=(0, 10))

        # 9. Default Pond Health Decay
        self._add_slider("default_decay", "9. Default Organic Decay (%/day)", 0.5, 10.0, 2.0, is_int=False)

        ctk.CTkFrame(self.sidebar, height=2, fg_color="gray30").pack(fill="x", padx=10, pady=15)

        # --- 10. TIMEFRAME & DATE CONTROLS ---
        self.lbl_timeframe = ctk.CTkLabel(self.sidebar, text="10. Date / Timeframe Filter", font=("Inter", 12, "bold"))
        self.lbl_timeframe.pack(anchor="w", padx=10, pady=(5, 5))

        # Start / End Date Entry Boxes
        date_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        date_frame.pack(fill="x", padx=5, pady=2)
        date_frame.grid_columnconfigure((0, 1), weight=1)

        self.entry_start_date = ctk.CTkEntry(date_frame, placeholder_text="Start: YYYY-MM-DD", width=120)
        self.entry_start_date.grid(row=0, column=0, padx=2, pady=2)
        self.entry_start_date.bind("<Return>", lambda e: self._on_date_entry_apply())

        self.entry_end_date = ctk.CTkEntry(date_frame, placeholder_text="End: YYYY-MM-DD", width=120)
        self.entry_end_date.grid(row=0, column=1, padx=2, pady=2)
        self.entry_end_date.bind("<Return>", lambda e: self._on_date_entry_apply())

        btn_apply_date = ctk.CTkButton(self.sidebar, text="Apply Date Filter", height=24, command=self._on_date_entry_apply)
        btn_apply_date.pack(fill="x", padx=10, pady=(2, 10))

        # Start Index Slider
        self.lbl_start_slider = ctk.CTkLabel(self.sidebar, text="Start Day: 0", font=("Inter", 11))
        self.lbl_start_slider.pack(anchor="w", padx=10)
        self.slider_start = ctk.CTkSlider(self.sidebar, from_=0, to=100, command=self._on_slider_timeframe_change)
        self.slider_start.set(0)
        self.slider_start.pack(fill="x", padx=10, pady=(0, 5))

        # End Index Slider
        self.lbl_end_slider = ctk.CTkLabel(self.sidebar, text="End Day: 0", font=("Inter", 11))
        self.lbl_end_slider.pack(anchor="w", padx=10)
        self.slider_end = ctk.CTkSlider(self.sidebar, from_=0, to=100, command=self._on_slider_timeframe_change)
        self.slider_end.set(100)
        self.slider_end.pack(fill="x", padx=10, pady=(0, 15))

        ctk.CTkFrame(self.sidebar, height=2, fg_color="gray30").pack(fill="x", padx=10, pady=10)

        # --- SUMMARY STATISTICS PANEL ---
        lbl_stats_header = ctk.CTkLabel(self.sidebar, text="📊 Summary Statistics", font=("Inter", 13, "bold"), text_color="#3a86ff")
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
                text="📁 Click Here to Load Historical Rainfall CSV File",
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

            self.chk_show_reactive = ctk.CTkCheckBox(
                ctrl_bar, text="Reactive Control", command=self._on_parameter_change,
                fg_color="#e63946", hover_color="#b82a36"
            )
            self.chk_show_reactive.select()
            self.chk_show_reactive.pack(side="left", padx=10)

            self.chk_show_predictive = ctk.CTkCheckBox(
                ctrl_bar, text="Predictive Setup", command=self._on_parameter_change,
                fg_color="#2a9d8f", hover_color="#1d6f65"
            )
            self.chk_show_predictive.select()
            self.chk_show_predictive.pack(side="left", padx=10)

            # PRE-CREATE OUTPUT FIGURE ONCE
            self.fig_out, self.ax_out = plt.subplots(figsize=(8, 5), dpi=100)
            self.fig_out.patch.set_facecolor('#2b2b2b')
            self.ax_out.set_facecolor('#1e1e1e')
            self.ax_out.text(0.5, 0.5, "Awaiting Data Upload & Configuration...", 
                            ha='center', va='center', color='gray')
            self.ax_out.set_xticks([])
            self.ax_out.set_yticks([])

            self.canvas_out = FigureCanvasTkAgg(self.fig_out, master=self.bottom_zone)
            self.canvas_out.get_tk_widget().pack(fill="both", expand=True, padx=10, pady=10)
    # ==========================================
    # 3. FILE LOADING & DATE HANDLING
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
                rain_series = pd.to_numeric(df[rain_col[0]], errors='coerce').fillna(0.0) if rain_col else pd.to_numeric(df.iloc[:, 0], errors='coerce').fillna(0.0)

                # Identify date column if present (FIX 1: Pass dayfirst=True)
                date_col = [c for c in df.columns if 'date' in c.lower() or 'day' in c.lower() or 'time' in c.lower()]
                if date_col:
                    self.date_column_name = date_col[0]
                    dates = pd.to_datetime(df[date_col[0]], dayfirst=True, errors='coerce').dt.strftime('%Y-%m-%d').fillna('')
                else:
                    self.date_column_name = None
                    dates = [f"Day {i+1}" for i in range(len(df))]

                self.df_data = pd.DataFrame({
                    'date': dates,
                    'rainfall_mm': rain_series
                })

                total_days = len(self.df_data)

                # Configure Sliders
                self.slider_start.configure(from_=0, to=total_days - 2, number_of_steps=total_days)
                self.slider_start.set(0)
                self.slider_end.configure(from_=1, to=total_days - 1, number_of_steps=total_days)
                self.slider_end.set(total_days - 1)

                # Mount canvas for top plot once if not already mounted
                if self.canvas_rain is None:
                    self.drop_label.destroy()
                    self.canvas_rain = FigureCanvasTkAgg(self.fig_rain, master=self.top_zone)
                    self.canvas_rain.get_tk_widget().pack(fill="both", expand=True, padx=5, pady=5)

                # Update Entry Text Boxes
                if self.date_column_name and self.df_data['date'].iloc[0] != '':
                    self.entry_start_date.delete(0, 'end')
                    self.entry_start_date.insert(0, self.df_data['date'].iloc[0])
                    self.entry_end_date.delete(0, 'end')
                    self.entry_end_date.insert(0, self.df_data['date'].iloc[-1])

                self._display_top_rainfall_graph()
                self._on_parameter_change()

            except Exception as e:
                self.drop_label.configure(text=f"❌ Error loading file: {str(e)}", text_color="red")
                
    def _display_top_rainfall_graph(self):
            # FIX 2: Clear existing axes instead of creating new subplots
            self.ax_rain.clear()

            start_idx = int(self.slider_start.get())
            end_idx = int(self.slider_end.get()) + 1
            sliced_data = self.df_data.iloc[start_idx:end_idx]

            days = range(start_idx + 1, end_idx + 1)
            self.ax_rain.bar(days, sliced_data['rainfall_mm'], color='#3a86ff', alpha=0.85)
            self.ax_rain.set_title(f"Rainfall Profile (Days {start_idx + 1} to {end_idx})", fontsize=10, color="white")
            self.ax_rain.set_ylabel("Rain (mm)", fontsize=8)
            self.ax_rain.grid(True, linestyle=":", alpha=0.3)
            self.fig_rain.tight_layout()

            if self.canvas_rain:
                self.canvas_rain.draw()

    def _on_slider_timeframe_change(self, val):
        start_idx = int(self.slider_start.get())
        end_idx = int(self.slider_end.get())

        if start_idx >= end_idx:
            start_idx = max(0, end_idx - 1)
            self.slider_start.set(start_idx)

        self.lbl_start_slider.configure(text=f"Start Day: {start_idx + 1} ({self.df_data['date'].iloc[start_idx]})")
        self.lbl_end_slider.configure(text=f"End Day: {end_idx + 1} ({self.df_data['date'].iloc[end_idx]})")

        self.entry_start_date.delete(0, 'end')
        self.entry_start_date.insert(0, str(self.df_data['date'].iloc[start_idx]))
        self.entry_end_date.delete(0, 'end')
        self.entry_end_date.insert(0, str(self.df_data['date'].iloc[end_idx]))

        self._display_top_rainfall_graph()
        self._on_parameter_change()

    def _on_date_entry_apply(self):
        if self.df_data is None:
            return

        start_str = self.entry_start_date.get().strip()
        end_str = self.entry_end_date.get().strip()

        # Match date string in dataframe
        start_matches = self.df_data[self.df_data['date'] == start_str].index
        end_matches = self.df_data[self.df_data['date'] == end_str].index

        if len(start_matches) > 0:
            self.slider_start.set(start_matches[0])
        if len(end_matches) > 0:
            self.slider_end.set(end_matches[0])

        self._on_slider_timeframe_change(None)

    # ==========================================
    # 4. COMPUTE & REDRAW ENGINE
    # ==========================================
    def _on_parameter_change(self):
        if self.df_data is None:
            return

        config = {key: meta["widget"].get() for key, meta in self.sliders.items()}
        config["decay_type"] = self.combo_decay.get()

        start_idx = int(self.slider_start.get())
        end_idx = int(self.slider_end.get()) + 1
        sliced_df = self.df_data.iloc[start_idx:end_idx]

        res_reactive, res_predictive = compute_simulation(sliced_df, config)

        self._update_statistics_display(res_reactive, res_predictive)
        self._render_output_graph(res_reactive, res_predictive, start_idx)

    def _update_statistics_display(self, res_r, res_p):
        stats_text = (
            f"=== REACTIVE CONTROL ===\n"
            f"• Days in Adverse : {res_r['adverse_days']} days\n"
            f"• Avg Pond Health : {res_r['avg_health']:.1f}%\n"
            f"• Total Water Used: {res_r['water_used']:.0f}%\n"
            f"• Emergency Resets: {res_r['adverse_breaches']}\n\n"
            f"=== PREDICTIVE SETUP ===\n"
            f"• Days in Adverse : {res_p['adverse_days']} days\n"
            f"• Avg Pond Health : {res_p['avg_health']:.1f}%\n"
            f"• Total Water Used: {res_p['water_used']:.0f}%\n"
            f"• Emergency Resets: {res_p['adverse_breaches']}"
        )
        self.lbl_stats_content.configure(text=stats_text)

    def _render_output_graph(self, res_reactive, res_predictive, start_idx):
        self.ax_out.clear()

        days = [start_idx + i + 1 for i in range(len(res_reactive["health"]))]

        show_r = self.chk_show_reactive.get()
        show_p = self.chk_show_predictive.get()

        if show_r:
            self.ax_out.plot(days, res_reactive["health"], color="#e63946", linewidth=2, label="Reactive Control")
        if show_p:
            self.ax_out.plot(days, res_predictive["health"], color="#2a9d8f", linewidth=2, label="Predictive Setup")

        self.ax_out.axhline(0, color="orange", linestyle="--", alpha=0.7, label="Adverse Boundary (0%)")
        self.ax_out.set_title("Pond Health Trajectory Comparison", fontsize=12, color="white")
        self.ax_out.set_xlabel("Timeline (Days)", fontsize=9)
        self.ax_out.set_ylabel("Pond Health Metric (%)", fontsize=9)
        self.ax_out.grid(True, linestyle=":", alpha=0.4)
        if show_r or show_p:
            self.ax_out.legend(loc="upper right")

        self.fig_out.tight_layout()
        self.canvas_out.draw()

# ==========================================
# 5. FIXED COMPUTE ENGINE
# ==========================================
def run_pond_step(current_health, daily_rain_mm, config, model_type, state):
    """
    Evaluates 1 day tick.
    - Non-Adverse Zone: Health decays strictly by 'default_decay'.
    - Adverse Zone: Non-linear decay curves (linear/quadratic/exponential/cubic) 
      are strictly isolated to periods where accumulated rain exceeds the adverse threshold.
    - Water Changes: Health restoration uses 'recovery_scalar' multiplier.
    """
    # 1. Base organic daily decay
    current_health -= config['default_decay']
    
    # 2. Accumulate rainfall with natural daily overflow (2mm/day drains out)
    state['accumulated_rain_mm'] = max(0.0, state['accumulated_rain_mm'] - 2.0) + daily_rain_mm
    
    # 3. ADVERSE ZONE DECAY (Strictly isolated to periods exceeding threshold)
    if state['accumulated_rain_mm'] > config['adverse_rain_threshold_mm']:
        excess_rain = state['accumulated_rain_mm'] - config['adverse_rain_threshold_mm']
        
        # Calculate adverse rain damage using the selected curve type
        if config['decay_type'] == 'linear':
            rain_damage = excess_rain * 3.0
        elif config['decay_type'] == 'quadratic':
            rain_damage = (excess_rain ** 2) * 0.5
        elif config['decay_type'] == 'exponential':
            rain_damage = (1.5 ** excess_rain)
        elif config['decay_type'] == 'cubic':
            rain_damage = (excess_rain ** 3) * 0.05
        else:
            rain_damage = excess_rain * 3.0
            
        current_health -= rain_damage
        state['is_adverse'] = True

    # 4. DECISION ENGINE
    water_changed_pct = 0.0
    
    # A. Emergency Reset (Day after entering Adverse Zone)
    if state['was_adverse_yesterday']:
        water_changed_pct = 100.0
        current_health = 100.0  # Full 100% reset
        state['accumulated_rain_mm'] = 0.0
        state['is_adverse'] = False
        state['days_since_last_change'] = 0
        state['adverse_breaches'] += 1
        
    # B. Predictive Partial Intervention (Predictive Model Only)
    elif model_type == 'PREDICTIVE' and (state['accumulated_rain_mm'] >= config['predictive_rain_trigger_mm']):
        water_changed_pct = config['predictive_change_pct']
        # Health restored using recovery_scalar multiplier
        current_health = min(100.0, current_health + (water_changed_pct * config['recovery_scalar']))
        state['accumulated_rain_mm'] *= (1.0 - (water_changed_pct / 100.0))
        state['days_since_last_change'] = 0
        
    # C. Default Monthly Maintenance Cycle
    elif state['days_since_last_change'] >= config[f'{model_type.lower()}_monthly_days']:
        water_changed_pct = config['monthly_change_pct']
        # Health restored using recovery_scalar multiplier
        current_health = min(100.0, current_health + (water_changed_pct * config['recovery_scalar']))
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
        'reactive_monthly_days': config['reactive_cycle_days'],
        'predictive_monthly_days': config['predictive_cycle_days'],
        'predictive_rain_trigger_mm': config['adverse_rain_mm'] * (config['predictive_trigger_pct'] / 100.0),
        'predictive_change_pct': config['predictive_change_pct'],
        'recovery_scalar': config['recovery_scalar'],
        'monthly_change_pct': config['monthly_change_pct'],
        'decay_type': config['decay_type'],
        'default_decay': config['default_decay']
    }

    state_reactive = {
        'accumulated_rain_mm': 0.0, 'is_adverse': False, 'was_adverse_yesterday': False,
        'days_since_last_change': 0, 'adverse_days_count': 0, 'adverse_breaches': 0
    }
    state_predictive = {
        'accumulated_rain_mm': 0.0, 'is_adverse': False, 'was_adverse_yesterday': False,
        'days_since_last_change': 0, 'adverse_days_count': 0, 'adverse_breaches': 0
    }

    health_r, health_p = 100.0, 100.0
    hist_r, hist_p = [], []
    water_r, water_p = 0.0, 0.0

    for day_idx in range(days):
        daily_rain = rain_data[day_idx]

        health_r, w_r, state_reactive = run_pond_step(health_r, daily_rain, model_config, 'REACTIVE', state_reactive)
        health_p, w_p, state_predictive = run_pond_step(health_p, daily_rain, model_config, 'PREDICTIVE', state_predictive)

        hist_r.append(health_r)
        hist_p.append(health_p)
        water_r += w_r
        water_p += w_p

    return (
        {
            "health": hist_r, 
            "water_used": water_r, 
            "adverse_days": state_reactive['adverse_days_count'],
            "avg_health": np.mean(hist_r),
            "adverse_breaches": state_reactive['adverse_breaches']
        },
        {
            "health": hist_p, 
            "water_used": water_p, 
            "adverse_days": state_predictive['adverse_days_count'],
            "avg_health": np.mean(hist_p),
            "adverse_breaches": state_predictive['adverse_breaches']
        }
    )


if __name__ == "__main__":
    app = PondSimulatorUI()
    app.mainloop()
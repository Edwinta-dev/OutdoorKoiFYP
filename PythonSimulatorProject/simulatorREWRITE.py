import dearpygui.dearpygui as dpg

dpg.create_context()
#Criteria
'''
1. Adverse zone rainfall in mm

2. Default monthly water change cycle for reactive

3. Default monthly water change cycle for predictive

4. Percentage into the adverse zone before predictive intervention

5. Percentage water change in a predictive intervention

6. Recovery scalar (If I change x proportion of water, how much health is restored)

7. Percentage water change for default monthly water change cycle

8. Type of decay in the adverse zone ( x mm more in the adverse zone = linear / quadratic / exponential / cubic decay in pond health metric)

9. Default pond health decay


'''
with dpg.window(label="Pond Simulator", width=1000, height=1000):
    adverse_rainfall = dpg.add_slider_int(label="Adverse Zone Rainfall (mm)", default_value=50, width=100, min_value=0, max_value=100)
    default_water_change_cycle = dpg.add_combo(label="Default Monthly Water Change Cycle (days)", items=[7,14,30,60,90], default_value=30)
    

    slider_int = dpg.add_slider_int(label="Slide to the left!", width=100)
    slider_float = dpg.add_slider_float(label="Slide to the right!", width=100)
dpg.create_viewport(title='Custom Title', width=600, height=200)
dpg.setup_dearpygui()
dpg.show_viewport()

# below replaces, start_dearpygui()
while dpg.is_dearpygui_running():
    # insert here any code you would like to run in the render loop
    # you can manually stop by using stop_dearpygui()
    print("this will run every frame")
    dpg.render_dearpygui_frame()

dpg.destroy_context()


#Evaluate and draw minutes in adverse health state
def run_pond_step(current_health, daily_rain_mm, config, model_type, state):
    """
    Evaluates 1 day tick for a pond model.
    """
    # 1. Base organic decay (Configuration #9)
    current_health -= config['default_decay']
    
    # 2. Add accumulated rain to current rain tracker
    state['accumulated_rain_mm'] += daily_rain_mm
    
    # 3. Calculate rainfall decay (Configurations #1 & #8)
    if state['accumulated_rain_mm'] > config['adverse_rain_threshold_mm']:
        excess_rain = state['accumulated_rain_mm'] - config['adverse_rain_threshold_mm']
        
        # Apply configured decay scaling
        if config['decay_type'] == 'linear':
            rain_damage = excess_rain * config['decay_scalar']
        elif config['decay_type'] == 'quadratic':
            rain_damage = (excess_rain ** 2) * config['decay_scalar']
        elif config['decay_type'] == 'exponential':
            rain_damage = (2 ** (excess_rain / 10.0)) * config['decay_scalar']
            
        current_health -= rain_damage
        state['is_adverse'] = True

    # 4. DECISION ENGINE
    water_changed_pct = 0.0
    
    # A. Forced Emergency Reset (Rule: Next day after adverse event)
    if state['was_adverse_yesterday']:
        water_changed_pct = 100.0
        current_health = 100.0  # Reset health
        state['accumulated_rain_mm'] = 0.0
        state['is_adverse'] = False
        
    # B. Predictive Partial Intervention (Predictive Model Only)
    elif model_type == 'PREDICTIVE' and (state['accumulated_rain_mm'] >= config['predictive_rain_trigger_mm']):
        water_changed_pct = config['predictive_change_pct']
        # Health restored by recovery scalar (Configuration #6)
        current_health = min(100.0, current_health + (water_changed_pct * config['recovery_scalar']))
        state['accumulated_rain_mm'] *= (1.0 - (water_changed_pct / 100.0)) # Partially clear rain effect
        
    # C. Default Monthly Maintenance Cycle
    elif state['days_since_last_change'] >= config[f'{model_type.lower()}_monthly_days']:
        water_changed_pct = config['monthly_change_pct']
        current_health = min(100.0, current_health + (water_changed_pct * config['recovery_scalar']))
        state['days_since_last_change'] = 0

    # Update state trackers
    state['was_adverse_yesterday'] = state['is_adverse']
    state['days_since_last_change'] += 1
    
    return max(-100.0, current_health), water_changed_pct, state

def draw_graph():
    pass
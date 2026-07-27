## --- TODO: Write HSV computing logic and state controller ---

DEFAULT_STATE = "base"
state = DEFAULT_STATE  # Global variable

def getstate():
    global state
    return state

def evalstate():
    global state  # Declare intent to modify the module-level global variable
    if state == "base":
        state = "dynamic"
        print("Pond in Dynamic image scheduling state")
    elif state == "dynamic":
        state = "obstruction"
        print("Pond in Obstruction image scheduling state")
    else:
        state = "base"
        print("Pond in Base image scheduling state")
    return state
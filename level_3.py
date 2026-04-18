import json
import os
from config_loader import load_config
from strategy_optimizer import optimize
from json_generator import generate_action

with open(r"C:\Users\Aspire\OneDrive\Desktop\Entelect Hack\3.txt") as f:
    data = json.load(f)

segments = data["track"]["segments"]
laps = data["race"]["laps"]
weather = data["weather"]["conditions"]

action = {
    "initial_tyre_id": 1,
    "laps": []
}

weather_tyre = {
    "dry": 1,
    "cold": 2,
    "light_rain": 4,
    "heavy_rain": 5
}

weather_index = 0
time_accum = 0

PIT_LAPS = [laps // 3, 2 * laps // 3]

for lap in range(1, laps + 1):
    lap_data = {"lap": lap, "segments": [], "pit": {"enter": False}}

    condition = weather[weather_index]["condition"]
    tyre_id = weather_tyre.get(condition, 1)

    if lap in PIT_LAPS:
        lap_data["pit"] = {
            "enter": True,
            "tyre_id": tyre_id,
            "fuel_to_add_l": 150
        }

    if condition == "dry":
        base_speed = 88
    elif condition == "cold":
        base_speed = 80
    elif condition == "light_rain":
        base_speed = 70
    else:
        base_speed = 60

    for seg in segments:
        if seg["type"] == "straight":
            length = seg["length_m"]
            brake = int(length * 0.28)

            lap_data["segments"].append({
                "id": seg["id"],
                "type": "straight",
                "target_m/s": base_speed,
                "brake_start_m_before_next": brake
            })
        else:
            lap_data["segments"].append({"id": seg["id"], "type": "corner"})

    action["laps"].append(lap_data)

with open("submission.txt", "w") as f:
    json.dump(action, f, indent=2)

print("Level 3 done")

def main():
    base_dir = os.getcwd()

    possible_paths = [
        os.path.join(base_dir, "3.txt"),
        os.path.join(base_dir, "inputs", "3.txt"),
        r"C:\Users\Aspire\Desktop\Entelect Hack\3.txt",
        r"C:\Users\Aspire\OneDrive\Desktop\Entelect Hack\3.txt"
    ]

    file_path = None

    for path in possible_paths:
        if os.path.exists(path):
            file_path = path
            break

    if file_path is None:
        print("ERROR: Could not find 3.txt")
        print("Checked paths:")
        for p in possible_paths:
            print(" -", p)
        return

    print("Using file:", file_path)

    race_data = load_config(file_path)

    best_strategy = optimize(race_data, iterations=200)

    print("\nBest Strategy Found")

    final_action = generate_action(best_strategy, race_data)

    output_path = os.path.join(base_dir, "submission.txt")

    with open(output_path, "w") as f:
        json.dump(final_action, f, indent=2)

    print(f"submission.txt saved at: {output_path}")

if __name__ == "__main__":
    main()
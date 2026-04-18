import json
import os
from config_loader import load_config
from strategy_optimizer import optimize
from json_generator import generate_action

with open(r"C:\Users\Aspire\OneDrive\Desktop\Entelect Hack\1.txt") as f:
    data = json.load(f)

segments = data["track"]["segments"]
laps = data["race"]["laps"]

action = {
    "initial_tyre_id": 1,
    "laps": []
}

for lap in range(1, laps + 1):
    lap_data = {
        "lap": lap,
        "segments": [],
        "pit": {"enter": False}
    }

    for seg in segments:
        if seg["type"] == "straight":
            length = seg["length_m"]

            if length > 800:
                brake = int(length * 0.28)
                speed = 88
            elif length > 600:
                brake = int(length * 0.26)
                speed = 86
            else:
                brake = int(length * 0.25)
                speed = 84

            lap_data["segments"].append({
                "id": seg["id"],
                "type": "straight",
                "target_m/s": speed,
                "brake_start_m_before_next": brake
            })
        else:
            lap_data["segments"].append({
                "id": seg["id"],
                "type": "corner"
            })

    action["laps"].append(lap_data)

with open("submission.txt", "w") as f:
    json.dump(action, f, indent=2)

print("Level 1 done")

def main():
    base_dir = os.getcwd()

    possible_paths = [
        os.path.join(base_dir, "1.txt"),
        os.path.join(base_dir, "inputs", "1.txt"),
        r"C:\Users\Aspire\Desktop\Entelect Hack\1.txt",
        r"C:\Users\Aspire\OneDrive\Desktop\Entelect Hack\1.txt"
    ]

    file_path = None

    for path in possible_paths:
        if os.path.exists(path):
            file_path = path
            break

    if file_path is None:
        print("ERROR: Could not find 1.txt")
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



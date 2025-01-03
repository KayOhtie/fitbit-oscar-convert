#!/usr/bin/env python3

from pathlib import Path
import struct
import sys
import csv
import json
from datetime import datetime, timedelta
from collections import defaultdict
from dateutil import tz


def minutes_to_time(minutes):
    return f"{int(minutes // 60):02d}:{int(minutes % 60):02d}:{int((minutes % 1) * 60):02d}"


class FitbitData:
    def __init__(self, path):
        self.path = path

    def export_spo2_as_viatom(self):
        timezone = self.read_profile_timezone()
        csv_files, json_files = self.get_spo2_files()
        if len(csv_files) == 0 or len(json_files) == 0:
            raise RuntimeError("No SpO2 or heart rate data detected!")

        sessions, data = self.align_spo2_data(csv_files, json_files, timezone)
        print("Detected SpO2 sessions:")
        for s in sessions:
            print(
                s[0].strftime("%Y-%m-%d %H:%M:%S"),
                "-",
                s[1].strftime("%Y-%m-%d %H:%M:%S"),
            )
        chunks = self.divide_data_to_viatom_chunks(sessions, data)

        for chunk in chunks:
            self.write_to_viatom_file(chunk)

    def export_sleep_phases_as_dreem(self):
        sleeppath = self.path / "Global Export Data"
        json_files = [file for file in sleeppath.glob("sleep-*.json")]
        if len(json_files) == 0:
            raise RuntimeError("No sleep data detected!")
        self.write_to_dreem(json_files)

    def read_profile_timezone(self):
        timezone = None
        with open(self.path / "Your Profile" / "Profile.csv", "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                timezone = row["timezone"]
        if timezone is not None:
            print("Timezone:", timezone)
        else:
            raise RuntimeError("Profile not detected!")
        return timezone

    def get_spo2_files(self):
        spo2_path = fitbit_path / "Oxygen Saturation (SpO2)"
        spo2_files = [file for file in spo2_path.glob("Minute SpO2*.csv")]
        bpm_path = fitbit_path / "Global Export Data"
        bpm_files = [file for file in bpm_path.glob("heart_rate-*.json")]
        return spo2_files, bpm_files

    def align_spo2_data(self, csv_files, json_files, timezone):
        def read_csv(file_name, timezone):
            with open(file_name, "r") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    utc_timestamp = datetime.strptime(
                        row["timestamp"], "%Y-%m-%dT%H:%M:%SZ"
                    )
                    utc_datetime = utc_timestamp.replace(tzinfo=tz.gettz('UTC'))
                    timestamp = utc_datetime.astimezone(tz.gettz(timezone))
                    value = round(float(row["value"]))
                    if value < 61:
                        continue
                    if value == 100:
                        value = 99
                    yield timestamp, value

        def read_json(file_name, timezone):
            with open(file_name, "r") as f:
                data = json.load(f)
                for entry in data:
                    utc_timestamp = datetime.strptime(
                        entry["dateTime"], "%m/%d/%y %H:%M:%S"
                    )
                    utc_datetime = utc_timestamp.replace(tzinfo=tz.gettz('UTC'))
                    timestamp = utc_datetime.astimezone(tz.gettz(timezone))
                    value = entry["value"]["bpm"]
                    yield timestamp, value

        data = defaultdict(lambda: [None, None])
        sessions = []
        for file_name in csv_files:
            for timestamp, value in read_csv(file_name, timezone):
                if len(sessions) == 0:
                    sessions.append([timestamp])
                else:
                    sessions_start = sessions[-1][0]
                    # start new sleep session if data points are at least 5 minutes apart
                    if timestamp - prev_timestamp > timedelta(minutes=5):
                        sessions[-1].append(prev_timestamp)
                        sessions.append([timestamp])
                data[timestamp][0] = value
                prev_timestamp = timestamp
        if len(sessions) == 0:
            raise RuntimeError("No SPO2 night sessions detected!")
        if len(sessions[-1]) == 1:
            sessions[-1].append(prev_timestamp)
        last_bpm_timestamp = None
        for file_name in json_files:
            for timestamp, value in read_json(file_name, timezone):
                data[timestamp][1] = value
                last_bpm_timestamp = timestamp
        filtered_sessions = []
        for s in sessions:
            if s[1] < last_bpm_timestamp:
                filtered_sessions.append(s)
        return filtered_sessions, data

    def divide_data_to_viatom_chunks(self, sessions, data):
        sorted_data = sorted(data.items())
        chunks = []
        chunk = []
        for session in sessions:
            last_timestamp = None
            for i in range(len(sorted_data) - 1):
                if last_timestamp is None:
                    timestamp = sorted_data[i][0]
                else:
                    timestamp = last_timestamp
                end_timestamp = sorted_data[i + 1][0]
                values = sorted_data[i][1]
                if values[0] is not None:
                    spo2 = values[0]
                if values[1] is not None:
                    bpm = values[1]
                if timestamp < session[0] or timestamp > session[1]:
                    continue
                records = 0
                while timestamp < end_timestamp:
                    if len(chunk) >= 4095:
                        chunks.append(chunk)
                        chunk = []
                    chunk.append((timestamp, spo2, bpm))
                    timestamp += timedelta(seconds=4)
                    records += 1
                last_timestamp = timestamp
            if chunk:
                chunks.append(chunk)
                chunk = []
        if chunk:
            chunks.append(chunk)
        return chunks

    def write_to_viatom_file(self, data):
        if len(data) > 4095:
            raise RuntimeError(
                f"Data chunk ({data[0][0]}, {data[-1][0]}) too long ({len(data)})!"
            )
        bin_file = "{}.bin".format(data[0][0].strftime("%Y%m%d%H%M%S"))
        with open(bin_file, "wb") as f:
            # Write header
            f.write(struct.pack("<BB", 0x5, 0x0))  # HEADER_LSB, HEADER_MSB
            f.write(struct.pack("<H", data[0][0].year))  # YEAR_LSB, YEAR_MSB
            f.write(
                struct.pack(
                    "<BBBBB",
                    data[0][0].month,
                    data[0][0].day,
                    data[0][0].hour,
                    data[0][0].minute,
                    data[0][0].second,
                )
            )  # MONTH, DAY, HOUR, MINUTES, SECONDS
            f.write(
                struct.pack("<I", len(data) * 5 + 40)
            )  # FILESIZE_0, FILESIZE_1, FILESIZE_2, 0x00
            f.write(struct.pack("<H", len(data) * 4))  # DURATION_LSB, DURATION_MSB
            f.write(b"\x00" * 25)  # Padding

            # Write records
            for record in data:
                if record[1] <= 61:
                    # print("TOOLOW:", record[1])
                    f.write(b"\xFF")
                    f.write(struct.pack("<B", record[2]))
                    f.write(b"\xFF\x00\x00")  # INVALID VALUE
                else:
                    if record[1] > 99:
                        print("TOOHIGH:", record[1])
                        f.write(struct.pack("<B", 99))  # MAX VALUE
                    else:
                        f.write(struct.pack("<B", record[1]))  # VALUE
                    f.write(struct.pack("<B", record[2]))
                    f.write(b"\x00\x00\x00")  # Padding

            print(
                "Exported",
                bin_file,
                f"(size: {len(data) * 5 + 40}, duration: {minutes_to_time(len(data)/15)})",
            )

    def generate_dreem_hypnogram(self, json_data):
        levels = {"wake": "WAKE", "rem": "REM", "light": "Light", "deep": "Deep"}
        sleep_stages = []
        for item in json_data:
            intervals = item["seconds"] // 30
            if item["level"] in levels:
                sleep_stages.extend([levels[item["level"]]] * intervals)
            else:
                print("Sleep stage '{}' is not recognized".format(item["level"]))
        return sleep_stages

    def write_to_dreem(self, json_files):
        with open("sleep.csv", "w", newline="") as csv_file:
            writer = csv.writer(csv_file, delimiter=";")
            writer.writerow(
                [
                    "Start Time",
                    "Stop Time",
                    "Sleep Onset Duration",
                    "Light Sleep Duration",
                    "Deep Sleep Duration",
                    "REM Duration",
                    "Wake After Sleep Onset Duration",
                    "Number of awakenings",
                    "Sleep efficiency",
                    "Hypnogram",
                ]
            )

            for file in json_files:
                with open(file, "r") as file:
                    json_data = json.load(file)
                    filtered_data = filter(lambda x: "light" in 
                            map(lambda y: y.lower(), x["levels"]["summary"].keys()),
                        json_data)
                    for item in filtered_data:
                        start_time = item["startTime"]
                        stop_time = item["endTime"]
                        print("Export to dreem sleep:", start_time, "-", stop_time)
                        sleep_onset_duration = minutes_to_time(item["duration"] / 60000)
                        light_sleep_duration = minutes_to_time(
                            item["levels"]["summary"]["light"]["minutes"]
                        )
                        deep_sleep_duration = minutes_to_time(
                            item["levels"]["summary"]["deep"]["minutes"]
                        )
                        rem_duration = minutes_to_time(
                            item["levels"]["summary"]["rem"]["minutes"]
                        )
                        wake_after_sleep_onset_duration = minutes_to_time(
                            item["minutesAwake"]
                        )
                        number_of_awakenings = item["levels"]["summary"]["wake"][
                            "count"
                        ]
                        sleep_efficiency = item["efficiency"]
                        hypnogram = self.generate_dreem_hypnogram(
                            item["levels"]["data"]
                        )

                        writer.writerow(
                            [
                                start_time,
                                stop_time,
                                sleep_onset_duration,
                                light_sleep_duration,
                                deep_sleep_duration,
                                rem_duration,
                                wake_after_sleep_onset_duration,
                                number_of_awakenings,
                                sleep_efficiency,
                                f"[{','.join(hypnogram)}]",
                            ]
                        )


def get_fitbit_path(s):
    fitbit_path = Path(s)
    if not fitbit_path.exists():
        raise RuntimeError(f"The path {fitbit_path} is not a valid directory.")
    if (fitbit_path / "Fitbit").exists():
        return fitbit_path / "Fitbit"
    elif (fitbit_path / "Takeout" / "Fitbit").exists():
        return fitbit_path / "Takeout" / "Fitbit"
    else:
        raise RuntimeError(
            f"The path {fitbit_path} does not contain Takeout/Fitbit directory."
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python fitbit.py <fitbit_path>")
        sys.exit(1)
    # try:
    fitbit_path = get_fitbit_path(sys.argv[1])
    fitbit_data = FitbitData(fitbit_path)
    fitbit_data.export_spo2_as_viatom()
    fitbit_data.export_sleep_phases_as_dreem()

    # except Exception as e:
    #     print("ERROR:", e)
    #     sys.exit(2)

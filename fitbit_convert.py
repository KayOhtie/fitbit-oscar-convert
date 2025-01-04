#!/usr/bin/env python3

from pathlib import Path
import re
import struct
import csv
import json
from datetime import datetime, timedelta, date
from collections import defaultdict
from dateutil import tz
import argparse
import logging

_l = logging.getLogger(__name__)

def minutes_to_time(minutes) -> str:
    return f"{int(minutes // 60):02d}:{int(minutes % 60):02d}:{int((minutes % 1) * 60):02d}"

def export_spo2_as_viatom(args):
    timezone = read_profile_timezone(args)
    csv_files, json_files = get_spo2_files(args)
    if len(csv_files) == 0 or len(json_files) == 0:
        raise FileNotFoundError("No SpO2 or heart rate data detected!")

    sessions, data = align_spo2_data(csv_files, json_files, timezone)
    _l.debug("Detected SpO2 sessions:")
    for s in sessions:
        _l.debug("".join([
            s[0].strftime("%Y-%m-%d %H:%M:%S"),
            "-",
            s[1].strftime("%Y-%m-%d %H:%M:%S"),])
        )
    chunks = divide_data_to_viatom_chunks(sessions, data)

    for chunk in chunks:
        write_to_viatom_file(args, chunk)

def export_sleep_phases_as_dreem(args):
    sleep_path = args.fitbit_path / "Global Export Data"
    json_files = [file for file in sleep_path.glob("sleep-*.json")]
    if len(json_files) == 0:
        raise FileNotFoundError("No sleep data detected!")
    write_to_dreem(args, json_files)

def read_profile_timezone(args) -> str:
    timezone = None
    with open(args.fitbit_path / "Your Profile" / "Profile.csv", "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            timezone = row["timezone"]
    # replacing if/then RuntimeError
    assert timezone is not None, "Profile not detected!"
    _l.debug("Timezone:", timezone)
    return timezone

def check_file_date(args, file) -> bool:
    """Ensures the file's date is within the threshold requested"""
    _l.debug(file.name)
    match = re.match(r".+(\d{4})-(\d\d)-(\d\d)\..+", file.name)
    file_date = date(year=int(match.group(1)), month=int(match.group(2)), day=int(match.group(3)))
    return args.start_date <= file_date <= args.end_date

def get_spo2_files(args) -> tuple[list]:
    spo2_path = args.fitbit_path / "Oxygen Saturation (SpO2)"
    spo2_files = [file for file in spo2_path.glob("Minute SpO2*.csv") if check_file_date(args, file)]
    bpm_path = args.fitbit_path / "Global Export Data"
    bpm_files = [file for file in bpm_path.glob("heart_rate-*.json") if check_file_date(args, file)]
    return spo2_files, bpm_files

def align_spo2_data(csv_files, json_files, timezone) -> tuple[list,dict]:
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
        raise FileNotFoundError("No SPO2 night sessions detected!")
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

def divide_data_to_viatom_chunks(sessions, data) -> list[list[tuple]]:
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

def write_to_viatom_file(args, data):
    if len(data) > 4095:
        raise RuntimeError(
            f"Data chunk ({data[0][0]}, {data[-1][0]}) too long ({len(data)})!"
        )
    bin_file = "{}.bin".format(data[0][0].strftime("%Y%m%d%H%M%S"))
    with open(args.export_path / bin_file, "wb") as f:
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
                _l.warning("TOOLOW:", record[1])
                f.write(b"\xFF")
                f.write(struct.pack("<B", record[2]))
                f.write(b"\xFF\x00\x00")  # INVALID VALUE
            else:
                if record[1] > 99:
                    _l.warning("TOOHIGH:", record[1])
                    f.write(struct.pack("<B", 99))  # MAX VALUE
                else:
                    f.write(struct.pack("<B", record[1]))  # VALUE
                f.write(struct.pack("<B", record[2]))
                f.write(b"\x00\x00\x00")  # Padding

        _l.info(
            f"Exported {bin_file} (size: {len(data) * 5 + 40}, duration: {minutes_to_time(len(data)/15)})"
        )

def generate_dreem_hypnogram(json_data) -> list:
    levels = {"wake": "WAKE", "rem": "REM", "light": "Light", "deep": "Deep"}
    sleep_stages = []
    for item in json_data:
        intervals = item["seconds"] // 30
        if item["level"] in levels:
            sleep_stages.extend([levels[item["level"]]] * intervals)
        else:
            _l.warning("Sleep stage '{}' is not recognized".format(item["level"]))
    return sleep_stages

def filter_sleep_data(args, sleep_record: dict[str,any]) -> bool:
    """Replaces the single lambda to match date of sleep information within requested range."""
    if "light" not in map(lambda y: y.lower(), sleep_record["levels"]["summary"].keys()):
        return False
    if "dateOfSleep" not in sleep_record.keys():
        return False
    sleep_date = date.fromisoformat(sleep_record["dateOfSleep"])
    return args.start_date <= sleep_date <= args.end_date

def write_to_dreem(args, json_files):
    with open(args.export_path / "sleep.csv", "w", newline="") as csv_file:
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
                filtered_data = filter(lambda x: filter_sleep_data(args,x),
                    json_data)
                for item in filtered_data:
                    start_time = item["startTime"]
                    stop_time = item["endTime"]
                    _l.info(f"Export to dreem sleep: {start_time} - {stop_time}")
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
                    hypnogram = generate_dreem_hypnogram(
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


def get_fitbit_path(s) -> Path:
    fitbit_path = Path(s)
    if not fitbit_path.exists():
        raise argparse.ArgumentError(f"The path {fitbit_path} is not a valid directory.")
    if (fitbit_path / "Fitbit").exists():
        return fitbit_path / "Fitbit"
    elif (fitbit_path / "Takeout" / "Fitbit").exists():
        return fitbit_path / "Takeout" / "Fitbit"
    else:
        raise argparse.ArgumentError(
            f"The path {fitbit_path} does not contain Takeout/Fitbit directory."
        )

def process_date_arg(datestring: str, argtype: str = 'file') -> date:
    datematch = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", datestring)
    if datematch == None:
        raise argparse.ArgumentError(f"Invalid {argtype} date argument '{datestring}', must match YYYY-M-D format")
    try:
        dateobj = date(year=int(datematch.group(1)), month=int(datematch.group(2)), day=int(datematch.group(3)))
        assert date.today() >= dateobj >= date(year=2010, month=1, day=1)
        if argtype == 'start':
            return dateobj - timedelta(days=1)
        elif argtype == 'end':
            return dateobj + timedelta(days=1)
        else:
            return dateobj
    except ValueError:
        raise argparse.ArgumentError(f"Invalid {argtype} date argument '{datestring}', must be a valid date")
    except AssertionError:
        raise argparse.ArgumentError(f"Invalid {argtype} date {datestring}, must be on or before today's date and no older than 2010-01-01.")

def get_verbosity(x) -> int:
    if x >= 2:
        return logging.DEBUG
    if x == 1:
        return logging.INFO
    else:
        return logging.WARNING

if __name__ == "__main__":
    script_start = datetime.now()
    parser = argparse.ArgumentParser()
    parser.add_argument("-s", "--start-date",
        metavar="<YYYY-M-D>",
        type=lambda x: process_date_arg(x,"start"),
        help="Optional start date for data",
        default=date(2010,1,1))
    parser.add_argument("-e", "--end-date",
        metavar="<YYYY-M-D>",
        type=lambda x: process_date_arg(x,"end"),
        help="Optional end date for data",
        default=date.today())
    parser.add_argument("-v", "--verbosity",
        action="count",
        help="increase output verbosity")
    parser.add_argument("-l","--logfile",
        metavar="<filename.log>",
        help="Log to file instead, implies single verbosity level (INFO)")
    parser.add_argument('fitbit_path',
        help="Path to Takeout folder containing 'Fitbit' or to Takeout folder",
        type=get_fitbit_path)
    parser.add_argument('export_path',
        help="Path to export files to, defaults to 'export' in current directory",
        type=lambda x: Path(x),
        nargs="?",
        default=Path('export'))
    args = parser.parse_args()
    logger_config = {
        'level': get_verbosity(args.verbosity),
        'format': "[{levelname[0]}] {asctime} - {message}",
        'style': "{",
        'datefmt': "%H:%M:%S"
    }
    if args.logfile is not None:
        logger_config['filename'] = Path(args.logfile) 
        logger_config['level'] = logging.INFO
    logging.basicConfig(**logger_config)
    if args.start_date > args.end_date:
        raise argparse.ArgumentError(f"Start date must be before or the same as the end date")
    try:
        export_path: Path = args.export_path
        if not export_path.exists():
            export_path.mkdir()
        export_spo2_as_viatom(args)
        export_sleep_phases_as_dreem(args)
        finish_message = f"Finished processing in {datetime.now() - script_start}"
        print(finish_message)
        if args.logfile is not None:
            _l.info(finish_message)
    except AssertionError as e:
        _l.fatal(f"Error processing data: {e}")
    except Exception as e:
        _l.exception(f"Unhandled exception: {e}")
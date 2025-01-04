# fitbit-oscar-convert

Fork of existing fitbit-convert script from a Google Drive link. Originally created by user 'ExtremeDeepSleep' [on the Apnea Board forums](https://www.apneaboard.com/forums/Thread-Fitbit-import-to-OSCAR).

Still only utilizes standard Python modules!

Notable changes include:

- Optional start/end date arguments for range of data to process
- Export path defaults to 'export' in current directory but can be specified
- Logging with verbosity levels, optionally log to file
- Fixes for sleep data processing with changes in level names

For help using the script,

```
$ python fitbit_convert.py -h
usage: fitbit_convert.py [-h] [-s <YYYY-M-D>] [-e <YYYY-M-D>] [-v] [-l <filename.log>] fitbit_path [export_path]

positional arguments:
  fitbit_path           Path to Takeout folder containing 'Fitbit' or to Takeout folder
  export_path           Path to export files to, defaults to 'export' in current directory

options:
  -h, --help            show this help message and exit
  -s <YYYY-M-D>, --start-date <YYYY-M-D>
                        Optional start date for data
  -e <YYYY-M-D>, --end-date <YYYY-M-D>
                        Optional end date for data
  -v, --verbosity       increase output verbosity
  -l <filename.log>, --logfile <filename.log>
                        Log to file instead, implies single verbosity level (INFO)
```
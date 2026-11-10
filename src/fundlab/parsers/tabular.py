"""Read bounded, explicitly mapped single-fund files."""

import csv
from dataclasses import dataclass
from datetime import datetime, time
from io import BytesIO, StringIO
from pathlib import PurePath
from zipfile import ZipFile

from openpyxl import load_workbook

from fundlab.errors import FundlabError
from fundlab.parsers.history import FIELD_DEFINITIONS, RATIO_FIELDS, parse_date, parse_number

MAX_BYTES = 10 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_ROWS = 50_000
MAX_COLUMNS = 100
MAX_ERRORS = 100
FIELDS = tuple(field[0] for field in FIELD_DEFINITIONS)
ALIASES = {field: {field, source, title} for field, source, title, _ in FIELD_DEFINITIONS}
for _field, _title in (('turnover', '成交额'), ('shares', '盘前份额'), ('shares_added', '盘前新增')):
    ALIASES[_field].add(_title)
JISILU_COLUMNS = {field: index for index, field in enumerate(FIELDS, 1)}


@dataclass(frozen=True)
class Cell:
    value: object
    percent: bool = False
    formula: bool = False


def _file_rows(filename, content, encoding, sheet, info):
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES:
        raise FundlabError('IMPORT_FILE_SIZE', '请选择非空且不超过 10 MiB 的文件。')
    extension = PurePath(filename).suffix.lower()
    if extension == '.csv':
        if encoding not in ('utf-8-sig', 'gb18030') or sheet is not None:
            raise FundlabError('IMPORT_OPTIONS', 'CSV 请选择 UTF-8 或 GB18030 编码。')
        info.update(format='csv', encoding=encoding)
        reader = csv.reader(StringIO(content.decode(encoding), newline=''), strict=True)
        for values in reader:
            yield tuple(Cell(value) for value in values)
        return
    if extension != '.xlsx':
        raise FundlabError('IMPORT_TYPE', '请选择 .xlsx 或 .csv 文件。')
    with ZipFile(BytesIO(content)) as archive:
        entries = archive.infolist()
        if len(entries) > 1000 or sum(entry.file_size for entry in entries) > MAX_EXPANDED:
            raise FundlabError('IMPORT_FILE_SIZE', 'Excel 文件展开后超出可处理大小。')
        if any(entry.flag_bits & 1 or 'vbaproject' in entry.filename.lower() for entry in entries):
            raise FundlabError('IMPORT_TYPE', '请选择无密码、无宏的 Excel 文件。')
        if b'macroenabled' in archive.read('[Content_Types].xml').lower():
            raise FundlabError('IMPORT_TYPE', '请选择无宏的 Excel 文件。')
    book = load_workbook(BytesIO(content), read_only=True, data_only=False, keep_links=False)
    try:
        selected = book.sheetnames[0] if sheet is None else sheet
        if selected not in book.sheetnames:
            raise FundlabError('IMPORT_OPTIONS', '所选工作表不存在。')
        info.update(format='xlsx', sheet=selected, sheets=book.sheetnames)
        tab = book[selected]
        if tab.max_column and tab.max_column > MAX_COLUMNS:
            raise FundlabError('IMPORT_FILE_SIZE', '表格不能超过 100 列。')
        if tab.max_row and tab.max_row > MAX_ROWS + 1:
            raise FundlabError('IMPORT_FILE_SIZE', '表格不能超过 50,001 行（含标题）。')
        # Ignore declared dimensions so misplaced rows or formulas are still checked.
        tab.reset_dimensions()
        for cells in tab.iter_rows():
            yield tuple(Cell(cell.value, cell.data_type == 'n' and '%' in (getattr(cell, 'number_format', '') or ''),
                             cell.data_type == 'f') for cell in cells)
    finally:
        book.close()


def _suggest(headers):
    result = {}
    for field, aliases in ALIASES.items():
        matches = [index for index, title in enumerate(headers, 1) if title in aliases]
        if len(matches) == 1:
            result[field] = matches[0]
    return result


def _value(field, cell, ratio_unit):
    value = cell.value
    if field in ('price_date', 'nav_date'):
        if isinstance(value, datetime) and value.time() == time():
            value = value.date()
        return parse_date(value, required=field == 'price_date')
    ratio = field in RATIO_FIELDS
    explicit_percent = isinstance(value, str) and value.strip().endswith('%')
    as_percent = ratio and (explicit_percent or (not cell.percent and ratio_unit == 'percent'))
    return parse_number(value, ratio=as_percent, nonnegative=field in {'close', 'nav', 'turnover', 'shares'})


def parse_table(filename, content, *, mapping=None, header_row=1, sheet=None,
                encoding='utf-8-sig', ratio_unit='percent', code=None):
    """Return normalized records and bounded row diagnostics, with one-based column indexes."""
    if (type(header_row) is not int or not 0 <= header_row <= 100
            or ratio_unit not in ('percent', 'fraction')):
        raise FundlabError('IMPORT_OPTIONS', '请核对标题行和比例单位。')
    if mapping is not None and (not isinstance(mapping, dict)
            or set(mapping) - set(FIELDS)
            or any(type(index) is not int or not 1 <= index <= MAX_COLUMNS for index in mapping.values())
            or len(set(mapping.values())) != len(mapping)):
        raise FundlabError('IMPORT_OPTIONS', '列映射有无效列或重复列。')
    records, errors, headers, seen = [], [], [], set()
    error_count = 0

    def report(row, field, message):
        nonlocal error_count
        error_count += 1
        if len(errors) < MAX_ERRORS:
            errors.append({'row': row, 'field': field, 'message': message})

    columns = dict(mapping) if mapping is not None else {}
    width = 0
    code_columns = []
    info = {'header_row': header_row, 'ratio_unit': ratio_unit}
    rows = _file_rows(filename, content, encoding, sheet, info)
    try:
        for number, cells in enumerate(rows, 1):
            if len(cells) > MAX_COLUMNS or number > MAX_ROWS + int(bool(header_row)):
                raise FundlabError('IMPORT_FILE_SIZE', '表格超过允许的行数或列数。')
            width = max(width, len(cells))
            if any(cell.formula for cell in cells):
                report(number, None, '请将公式转换为数值后再导入。')
                continue
            if number == header_row:
                headers = [str(cell.value).strip()[:160] if cell.value is not None else '' for cell in cells]
                code_columns = [index for index, title in enumerate(headers)
                                if title in ('基金代码', 'fund_id', 'fund_code')]
                if mapping is None:
                    columns = _suggest(headers)
                continue
            if number < header_row or all(cell.value is None or str(cell.value).strip() == '' for cell in cells):
                continue
            if 'price_date' not in columns:
                continue
            values = dict.fromkeys(FIELDS)
            before = error_count
            if code is not None and any(index >= len(cells) or str(cells[index].value).strip() != code
                                        for index in code_columns):
                report(number, 'code', '文件内基金代码与选定基金不一致，请核对前导零。')
            for field, index in columns.items():
                try:
                    values[field] = _value(field, cells[index - 1] if index <= len(cells) else Cell(None), ratio_unit)
                except (ValueError, TypeError, ArithmeticError):
                    report(number, field, '日期、数值或单位无效。')
            day = values['price_date']
            if day is not None:
                if day in seen:
                    report(number, 'price_date', '文件内价格日期重复。')
                seen.add(day)
            if error_count == before:
                records.append((number, values))
    except FundlabError:
        raise
    except Exception as error:
        raise FundlabError('IMPORT_READ_FAILED', '无法读取表格，请检查格式、编码或文件是否损坏。') from error
    finally:
        rows.close()
    headers.extend([''] * (width - len(headers)))
    if 'price_date' not in columns:
        report(header_row or None, 'price_date', '请指定价格日期列。')
    if columns and max(columns.values()) > width:
        report(header_row or None, None, '映射列超出表格范围。')
    if not records and not error_count:
        report(None, None, '表格中没有可导入的记录。')
    return {'headers': headers, 'mapping': columns, 'options': info, 'records': records,
            'presets': {'jisilu_qdii': JISILU_COLUMNS.copy()},
            'errors': errors, 'error_count': error_count}

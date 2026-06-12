/** Parse batch-run input (C8.1) into an array of payload objects.
 *
 *  Accepts either a JSON array of objects (text starting with `[`) or CSV with a
 *  header row. CSV cells are kept as strings; downstream workflow config paths
 *  read them as-is. Throws a human-readable Error on malformed input. */
export function parseBatchInput(text: string): Record<string, unknown>[] {
  const trimmed = text.trim();
  if (!trimmed) throw new Error('No data — paste JSON or CSV, or choose a file.');

  // A leading { or [ signals JSON intent; a bare object is rejected as not-an-array
  // rather than mis-parsed as CSV.
  if (trimmed[0] === '[' || trimmed[0] === '{') {
    let parsed: unknown;
    try {
      parsed = JSON.parse(trimmed);
    } catch (e) {
      throw new Error(`Invalid JSON: ${(e as Error).message}`);
    }
    if (!Array.isArray(parsed)) throw new Error('JSON must be an array of objects.');
    if (parsed.some((r) => r === null || typeof r !== 'object' || Array.isArray(r))) {
      throw new Error('Every JSON array item must be an object.');
    }
    if (parsed.length === 0) throw new Error('The array is empty — add at least one row.');
    return parsed as Record<string, unknown>[];
  }

  return parseCsv(trimmed);
}

function parseCsv(text: string): Record<string, unknown>[] {
  const records = csvRecords(text).filter((cells) => cells.some((c) => c !== ''));
  if (records.length < 2) {
    throw new Error('CSV needs a header row and at least one data row.');
  }
  const headers = records[0];
  if (headers.some((h) => h === '')) throw new Error('CSV header has an empty column name.');
  return records.slice(1).map((cells) => {
    const row: Record<string, unknown> = {};
    headers.forEach((h, i) => {
      row[h] = cells[i] ?? '';
    });
    return row;
  });
}

/** Tokenize CSV into records of cells in one character-level pass, honoring
 *  double-quoted fields — including `""` escapes and commas *and newlines*
 *  inside quotes (so multiline cells parse instead of corrupting rows). */
function csvRecords(text: string): string[][] {
  const records: string[][] = [];
  let cells: string[] = [];
  let cur = '';
  let inQuotes = false;

  const endCell = () => {
    cells.push(cur.trim());
    cur = '';
  };
  const endRecord = () => {
    endCell();
    records.push(cells);
    cells = [];
  };

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (inQuotes) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          cur += '"';
          i++;
        } else {
          inQuotes = false;
        }
      } else {
        cur += ch;
      }
    } else if (ch === '"') {
      inQuotes = true;
    } else if (ch === ',') {
      endCell();
    } else if (ch === '\n') {
      endRecord();
    } else if (ch !== '\r') {
      cur += ch;
    }
  }
  if (inQuotes) throw new Error('CSV has an unterminated quoted field.');
  endRecord();
  return records;
}

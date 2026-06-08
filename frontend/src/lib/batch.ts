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
  const lines = text.split(/\r?\n/).filter((l) => l.trim() !== '');
  if (lines.length < 2) {
    throw new Error('CSV needs a header row and at least one data row.');
  }
  const headers = splitCsvLine(lines[0]);
  if (headers.some((h) => h === '')) throw new Error('CSV header has an empty column name.');
  return lines.slice(1).map((line) => {
    const cells = splitCsvLine(line);
    const row: Record<string, unknown> = {};
    headers.forEach((h, i) => {
      row[h] = cells[i] ?? '';
    });
    return row;
  });
}

/** Split one CSV line, honoring double-quoted fields (with `""` escapes and
 *  commas inside quotes). */
function splitCsvLine(line: string): string[] {
  const out: string[] = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuotes) {
      if (ch === '"') {
        if (line[i + 1] === '"') {
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
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out.map((s) => s.trim());
}

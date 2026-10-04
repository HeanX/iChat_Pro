const assert = require('node:assert/strict');

// Compare the actual PE icon group and image bytes, independent of Shell caches.
module.exports = function verifyIcon(executable, ico) {
  const pe = executable.readUInt32LE(0x3c);
  assert.equal(executable.readUInt32LE(pe), 0x4550, 'Require a PE executable');
  const optional = pe + 24;
  const directories = optional + (executable.readUInt16LE(optional) === 0x20b ? 112 : 96);
  const resourceRva = executable.readUInt32LE(directories + 16);
  const sections = optional + executable.readUInt16LE(pe + 20);
  function fileOffset(rva) {
    for (let i = 0; i < executable.readUInt16LE(pe + 6); i += 1) {
      const section = sections + i * 40;
      const start = executable.readUInt32LE(section + 12);
      const size = Math.max(executable.readUInt32LE(section + 8), executable.readUInt32LE(section + 16));
      if (rva >= start && rva < start + size) {
        return executable.readUInt32LE(section + 20) + rva - start;
      }
    }
    throw new Error(`Resource RVA ${rva} is outside PE sections`);
  }
  assert.ok(resourceRva, 'Executable must have resources');
  const root = fileOffset(resourceRva);
  function entries(relative) {
    const directory = root + relative;
    const count = executable.readUInt16LE(directory + 12) + executable.readUInt16LE(directory + 14);
    return Array.from({ length: count }, (_, i) => {
      const entry = directory + 16 + i * 8;
      return { id: executable.readUInt32LE(entry), target: executable.readUInt32LE(entry + 4) };
    });
  }
  function payloads(target) {
    if (target & 0x80000000) {
      return entries(target & 0x7fffffff).flatMap(entry => payloads(entry.target));
    }
    const data = root + target;
    const start = fileOffset(executable.readUInt32LE(data));
    return [executable.subarray(start, start + executable.readUInt32LE(data + 4))];
  }
  function resources(typeId) {
    const type = entries(0).find(entry => entry.id === typeId);
    assert.ok(type, `Missing PE resource type ${typeId}`);
    return new Map(entries(type.target & 0x7fffffff).map(entry => [entry.id, payloads(entry.target)]));
  }
  const images = resources(3);
  const groups = resources(14);
  assert.equal(ico.readUInt16LE(2), 1, 'Require an ICO file');
  const count = ico.readUInt16LE(4);
  assert.ok(count > 0, 'Require at least one source icon frame');
  const matches = [...groups.values()].flat().some(group => {
    if (group.readUInt16LE(4) !== count) return false;
    for (let i = 0; i < count; i += 1) {
      const source = 6 + i * 16;
      const entry = 6 + i * 14;
      if (group[entry] !== ico[source] || group[entry + 1] !== ico[source + 1]) return false;
      const start = ico.readUInt32LE(source + 12);
      const expected = ico.subarray(start, start + ico.readUInt32LE(source + 8));
      const candidates = images.get(group.readUInt16LE(entry + 12)) || [];
      if (!candidates.some(actual => actual.equals(expected))) return false;
    }
    return true;
  });
  assert.ok(matches, 'Native exe icon group must contain every source ICO frame');
  return count;
};

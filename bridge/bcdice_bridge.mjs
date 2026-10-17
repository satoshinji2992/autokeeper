// JSON-lines bridge: Python writes {"id", "system", "command"} per line,
// we answer with BCDice's result on one line. Game systems are loaded once and cached.
import readline from 'node:readline';
import pkg from 'bcdice';

const { DynamicLoader, Version } = pkg;
const loader = new DynamicLoader();
const systems = new Map();

async function system(id) {
  if (!systems.has(id)) systems.set(id, await loader.dynamicLoad(id));
  return systems.get(id);
}

function reply(obj) {
  process.stdout.write(JSON.stringify(obj) + '\n');
}

const rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
reply({ id: 0, ok: true, ready: true, version: Version });

rl.on('line', async (line) => {
  let req;
  try {
    req = JSON.parse(line);
  } catch {
    reply({ id: null, ok: false, reason: 'bad json' });
    return;
  }
  try {
    const game = await system(req.system || 'Cthulhu7th:ChineseTraditional');
    const r = game.eval(req.command);
    if (!r) {
      reply({ id: req.id, ok: false, reason: 'unsupported command' });
      return;
    }
    reply({
      id: req.id,
      ok: true,
      text: r.text,
      secret: r.secret,
      success: r.success,
      failure: r.failure,
      critical: r.critical,
      fumble: r.fumble,
      rands: r.rands,
    });
  } catch (err) {
    reply({ id: req.id, ok: false, reason: String(err && err.message || err) });
  }
});

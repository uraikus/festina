// claude.md #70 (DatabaseURL), #71 (environment.NAME) -- must be the
// very first line of this file, before any other code or import. Build
// and run with:
//
//   ./bin/festina examples/config.f -o config_demo
//   FESTINA_DB_PATH=custom.sqlite ./config_demo
//   ./config_demo                                 # falls back to festina.sqlite
DatabaseURL = environment.FESTINA_DB_PATH

table Visits {
    id:int
    place:text
}

sqlite('INSERT INTO Visits (id, place) VALUES (?, ?)', [1, 'the summit'])
arr[Visits] visits = sqlite('SELECT * FROM Visits')
log(`visit ${visits[0].id}: ${visits[0].place}`)

// environment.NAME reads any environment variable as text, or null if
// it isn't set -- read-only, and (unlike DatabaseURL) usable anywhere,
// not just the entry file's first line.
text apiKey = environment.API_KEY
if apiKey == null {
    log('API_KEY is not set (that is fine -- this is just a demo)')
} else {
    log(`API_KEY is set`)
}

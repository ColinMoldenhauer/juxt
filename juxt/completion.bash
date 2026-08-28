# Bash completion for juxt.  Enable it with
#
#     eval "$(juxt-complete --bash)"          # in ~/.bashrc
#
# zsh can reuse it through bashcompinit:
#
#     autoload -U bashcompinit && bashcompinit
#     eval "$(juxt-complete --bash)"
#
# Path arguments complete with {placeholders} left in place, exactly like the
# :pattern command bar inside the app: `juxt plots/{sensor}/2<TAB>` lists what
# every sensor directory holds, so a template can be built top-down.

_juxt_completion() {
    local cur prev helper line
    cur=${COMP_WORDS[COMP_CWORD]}
    prev=""
    ((COMP_CWORD > 0)) && prev=${COMP_WORDS[COMP_CWORD - 1]}

    COMPREPLY=()
    helper=${JUXT_COMPLETE:-juxt-complete}
    if ! command -v "$helper" >/dev/null 2>&1; then
        # juxt lives in an environment that is not active — plain filenames.
        while IFS= read -r line; do COMPREPLY+=("$line"); done \
            < <(compgen -f -- "$cur")
        return
    fi

    while IFS= read -r line; do
        [[ -n $line ]] && COMPREPLY+=("$line")
    done < <("$helper" -- "$prev" "$cur" 2>/dev/null)

    # -o nospace keeps a completed directory open; finish any other word.
    if ((${#COMPREPLY[@]} == 1)) && [[ ${COMPREPLY[0]} != */ ]]; then
        COMPREPLY[0]+=" "
    fi
}

complete -o nospace -F _juxt_completion juxt

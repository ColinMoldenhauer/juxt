# Bash completion for juxt.  Enable it with
#
#     eval "$(juxt --bash-completion)"        # in ~/.bashrc
#
# zsh can reuse the same function through bashcompinit:
#
#     autoload -U bashcompinit && bashcompinit
#     eval "$(juxt --bash-completion)"
#
# Path arguments complete with {placeholders} left in place, exactly like the
# :pattern command bar inside the app: juxt plots/{sensor}/2<TAB> lists what
# every sensor directory holds, so a template can be built top-down.
#
# Candidates come from juxt-complete when it is on PATH (it starts in
# milliseconds because it never imports Qt), otherwise from juxt itself.
# Set JUXT_COMPLETE to the full path of the helper when juxt lives in a
# virtualenv that is not active.

_juxt_completion() {
    local cur prev out status line
    local -a helper=()

    cur=${COMP_WORDS[COMP_CWORD]}
    prev=""
    ((COMP_CWORD > 0)) && prev=${COMP_WORDS[COMP_CWORD - 1]}

    if [[ -n ${JUXT_COMPLETE:-} ]]; then
        helper=("$JUXT_COMPLETE" --)
    elif command -v juxt-complete >/dev/null 2>&1; then
        helper=(juxt-complete --)
    elif command -v juxt >/dev/null 2>&1; then
        helper=(juxt --complete-words)
    fi

    COMPREPLY=()
    if ((${#helper[@]} > 0)); then
        out=$("${helper[@]}" "$prev" "$cur" 2>/dev/null)
        status=$?
    else
        status=1
    fi

    if ((status != 0)); then
        # No helper, or one too old to understand us — plain filenames.
        while IFS= read -r line; do COMPREPLY+=("$line"); done \
            < <(compgen -f -- "$cur")
        return
    fi

    while IFS= read -r line; do
        [[ -n $line ]] && COMPREPLY+=("$line")
    done <<< "$out"

    # -o nospace keeps a completed directory open; finish any other word.
    if ((${#COMPREPLY[@]} == 1)) && [[ ${COMPREPLY[0]} != */ ]]; then
        COMPREPLY[0]+=" "
    fi
}

complete -o nospace -F _juxt_completion juxt

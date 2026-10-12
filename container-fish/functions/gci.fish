function gci --wraps='git branchless record -i' --description 'Do an interactive git commit like jj'
    git branchless record -i $argv
end

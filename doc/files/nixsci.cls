\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{nixsci}[nixsci template class]

\def\nixsci@inclass{}
\RequirePackage{nixsci}

\newcommand\nixsci@docopts{}
\DeclareOption*{\g@addto@macro\nixsci@docopts{,\CurrentOption}}
\ProcessOptions\relax

\ifx\nixsci@tclass\@empty
  \ClassError{nixsci}{no template class}{The document's template names no class (a plain template\MessageBreak
  is used with \string\usepackage{nixsci} under a class the document names).}%
\fi

\LoadClass[\nixsci@tclassopts\nixsci@docopts]{\nixsci@tclass}

\nixsci@loadall
\endinput

// Copyright (c)  Mingcheng Zuo, Dietmar Wolz.
//
// This source code is licensed under the MIT license found in the
// LICENSE file in the root directory.

// Eigen based implementation of differential evolution (GCL-DE) derived from
// "A case learning-based differential evolution algorithm for global optimization of interplanetary trajectory design,
//  Mingcheng Zuo, Guangming Dai, Lei Peng, Maocai Wang, Zhengquan Liu", https://doi.org/10.1016/j.asoc.2020.106451

#include <Eigen/Core>
#include <iostream>
#include <float.h>
#include <ctime>
#include <random>
#include "pcg_random.hpp"

using namespace std;

typedef Eigen::Matrix<double, Eigen::Dynamic, 1> vec;
typedef Eigen::Matrix<int, Eigen::Dynamic, 1> ivec;
typedef Eigen::Matrix<double, Eigen::Dynamic, Eigen::Dynamic> mat;

typedef void (*callback_parallel)(int, int, double[], double[]);

namespace gcl_differential_evolution {

static uniform_real_distribution<> distr_01 = std::uniform_real_distribution<>(
		0, 1);
static normal_distribution<> gauss_01 = std::normal_distribution<>(0, 1);

static double normreal(pcg64 *rs, double mu, double sdev) {
	return gauss_01(*rs) * sdev + mu;
}

static vec zeros(int n) {
	return Eigen::MatrixXd::Zero(n, 1);
}

static vec constant(int n, double val) {
	return Eigen::MatrixXd::Constant(n, 1, val);
}

static Eigen::MatrixXd uniformVec(int dim, pcg64 &rs) {
	return Eigen::MatrixXd::NullaryExpr(dim, 1, [&]() {
		return distr_01(rs);
	});
}

static Eigen::MatrixXd uniform(int dx, int dy, pcg64 &rs) {
	return Eigen::MatrixXd::NullaryExpr(dx, dy, [&]() {
		return distr_01(rs);
	});
}

struct IndexVal {
	int index;
	double val;
};

static bool compareIndexVal(IndexVal i1, IndexVal i2) {
	return (i1.val < i2.val);
}

static ivec sort_index(const vec &x) {
	int size = x.size();
	IndexVal ivals[size];
	for (int i = 0; i < size; i++) {
		ivals[i].index = i;
		ivals[i].val = x[i];
	}
	std::sort(ivals, ivals + size, compareIndexVal);
	return Eigen::MatrixXi::NullaryExpr(size, 1, [&ivals](int i) {
		return ivals[i].index;
	});
}


// wrapper around the fittness function, scales according to boundaries

class Fittness {

public:

	Fittness(callback_parallel func_par_, const vec &lower_limit,
			const vec &upper_limit) {
		func_par = func_par_;
		lower = lower_limit;
		upper = upper_limit;
		evaluationCounter = 0;
		if (lower.size() > 0) { // bounds defined
			scale = (upper - lower);
			invScale = scale.cwiseInverse();
		}
	}

	vec getClosestFeasible(const vec &X) const {
		if (lower.size() > 0) {
			return X.cwiseMin(upper).cwiseMax(lower);
		}
		return X;
	}

	void values(const mat &popX, vec &ys) {
		int popsize = popX.cols();
		int n = popX.rows();
		double pargs[popsize*n];
		double res[popsize];
		for (int p = 0; p < popX.cols(); p++) {
			for (int i = 0; i < n; i++)
				pargs[p * n + i] = popX(i, p);
		}
		func_par(popsize, n, pargs, res);
		for (int p = 0; p < popX.cols(); p++)
			ys[p] = res[p];
		evaluationCounter += popsize;
	}

	double distance(const vec &x1, const vec &x2) {
		return ((x1 - x2).array() * invScale.array()).matrix().squaredNorm();
	}

	bool feasible(int i, double x) {
		return x >= lower[i] && x <= upper[i];
	}

	vec uniformX(pcg64 &rs) {
		vec rv = uniformVec(lower.size(), rs);
		return (rv.array() * scale.array()).matrix() + lower;
	}

	double uniformXi(int i, pcg64 &rs) {
		return lower[i] + scale[i] * distr_01(rs);
	}

	int getEvaluations() {
		return evaluationCounter;
	}

private:
	callback_parallel func_par;
	vec lower;
	vec upper;
	long evaluationCounter;
	vec scale;
	vec invScale;
};

class GclDeOptimizer {

public:

	GclDeOptimizer(long runid_, Fittness *fitfun_, int dim_, int seed_,
			int popsize_, int maxEvaluations_, double pbest_,
			double stopfitness_, double F0_, double CR0_) {
		// runid used to identify a specific run
		runid = runid_;
		// fitness function to minimize
		fitfun = fitfun_;
		// Number of objective variables/problem dimension
		dim = dim_;
		// Population size
		popsize = popsize_ > 0 ? popsize_ : 15 * dim;
		// maximal number of evaluations allowed.
		maxEvaluations = maxEvaluations_;
		// use low value 0 < pbest <= 1 to narrow search.
		pbest = pbest_;
		// Limit for fitness value.
		stopfitness = stopfitness_;
		F0 = F0_;
		CR0 = CR0_;
		// stop criteria
		stop = 0;
		rs = new pcg64(seed_);
		init();
	}

	~GclDeOptimizer() {
		delete rs;
	}

	double rnd01() {
		return distr_01(*rs);
	}

	int rndInt(int max) {
		return (int) (max * distr_01(*rs));
	}

	int rndInt2(int max) {
		double u = distr_01(*rs);
		return (int) (max * u*u);
	}

	void doOptimize() {
		int gen_stuck = 0;
		vector<vec> sp;

		int maxIter = maxEvaluations / popsize + 1;
		double previous_best = DBL_MAX;
		double CR, F;

		// -------------------- Generation Loop --------------------------------

		for (iterations = 1;; iterations++) {
			// sort population
			ivec sindex = sort_index(nextY);
			popY = nextY(sindex, Eigen::all);
			popX = nextX(Eigen::all, sindex);

			bestX = popX.col(0);
			bestY = popY[0];

			if (isfinite(stopfitness) && bestY < stopfitness) {
				stop = 1;
				return;
			}
			if (bestY == previous_best)
				gen_stuck++;
			else
				gen_stuck = 0;
			previous_best = bestY;

			if (fitfun->getEvaluations() >= maxEvaluations)
				return;
			int max_r2 = max(3, int(popsize * pbest));
			for (int p = 0; p < popsize; p++) {
				int r1, r2, r3;
				do {
					r1 = rndInt(popsize);
				} while (r1 == p);
				do {
					r2 = rndInt2(max_r2);
				} while (r2 == p || r2 == r1);
				do {
					r3 = rndInt(popsize + sp.size());
				} while (r3 == p || r3 == r2 || r3 == r1);
				int jr = rndInt(dim);
				//Produce the CR and F
				double mu = 1
						- sqrt(float(iterations / maxIter))
								* exp(float(-gen_stuck / iterations));
				if (iterations % 2 == 1) {
					CR = normreal(rs, 0.90, 0.02);
					F = normreal(rs, mu, 0.2);
					if (F < 0 || F > 1)
						F = rnd01();
				} else {
					CR = CR0;
					F = F0;
				}
				vec ui = popX.col(p);
				for (int j = 0; j < dim; j++) {
					if (j == jr || rnd01() < CR) {
						if (r3 < popsize)
							ui[j] = popX(j, r1)
									+ F * (popX(j, r2) - popX(j, r3));
						else
							ui[j] = popX(j, r1)
									+ F * ((popX)(j, r2) - sp[r3 - popsize][j]);
						if (!fitfun->feasible(j, ui[j]))
							ui[j] = fitfun->uniformXi(j, *rs);
					}
				}
				nextX.col(p) = ui;
			}
			fitfun->values(nextX, nextY);
			for (int p = 0; p < popsize; p++) {
				if (nextY[p] < popY[p]) {
					if (sp.size() < popsize)
						sp.push_back(popX.col(p));
					else
						sp[rndInt(popsize)] = popX.col(p);
				} else {        // no improvement, copy from parent
					nextX.col(p) = popX.col(p);
					nextY[p] = popY[p];
				}
			}
		}
	}

	void init() {
		popCR = zeros(popsize);
		popF = zeros(popsize);
		nextX = mat(dim, popsize);
		for (int p = 0; p < popsize; p++)
			nextX.col(p) = fitfun->uniformX(*rs);
		nextY = vec(popsize);
		fitfun->values(nextX, nextY);
	}

	vec getBestX() {
		return bestX;
	}

	double getBestValue() {
		return bestY;
	}

	double getIterations() {
		return iterations;
	}

	double getStop() {
		return stop;
	}

private:
	long runid;
	Fittness *fitfun;
	int popsize; // population size
	int dim;
	int maxEvaluations;
	double pbest;
	double stopfitness;
	int iterations;
	double bestY;
	vec bestX;
	int stop;
	double F0;
	double CR0;
	pcg64 *rs;
	mat popX;
	vec popY;
	mat nextX;
	vec nextY;
	vec popCR;
	vec popF;
};
}

using namespace gcl_differential_evolution;

extern "C" {
double* optimizeGCLDE_C(long runid, callback_parallel func_par, int dim, int seed,
		double *lower, double *upper, int maxEvals, double pbest,
		double stopfitness, int popsize, double F0, double CR0) {
	int n = dim;
	double *res = new double[n + 4];
	vec lower_limit(n), upper_limit(n);
	bool useLimit = false;
	for (int i = 0; i < n; i++) {
		lower_limit[i] = lower[i];
		upper_limit[i] = upper[i];
		useLimit |= (lower[i] != 0);
		useLimit |= (upper[i] != 0);
	}
	if (useLimit == false) {
		lower_limit.resize(0);
		upper_limit.resize(0);
	}
	Fittness fitfun(func_par, lower_limit, upper_limit);
	GclDeOptimizer opt(runid, &fitfun, dim, seed, popsize, maxEvals, pbest,
			stopfitness, F0, CR0);
	try {
		opt.doOptimize();
		vec bestX = opt.getBestX();
		double bestY = opt.getBestValue();
		for (int i = 0; i < n; i++)
			res[i] = bestX[i];
		res[n] = bestY;
		res[n + 1] = fitfun.getEvaluations();
		res[n + 2] = opt.getIterations();
		res[n + 3] = opt.getStop();
		return res;
	} catch (std::exception &e) {
		cout << e.what() << endl;
		return res;
	}
}
}
